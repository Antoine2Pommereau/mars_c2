"""Faux S3 des essais de lecture : chaque requête doit porter une signature AWS version 4 par en tête valide pour la
paire de l'essai (clés factices), sinon 403 SignatureDoesNotMatch, comme eodata. Bibliothèque standard seulement
(Python 3.8 des images d'AI2) ; jamais déployé.

Les fichiers viennent d'un dossier local (`racine`), ou d'un miroir HTTP (`miroir`) : la requête signée pour
/eodata/<chemin> est relayée, plage d'octets comprise, vers <miroir><chemin> ; on lit ainsi de vraies tuiles
Sentinel 2 par requêtes partielles sans les télécharger en entier. Octets envoyés, requêtes et refus sont comptés.

Utilisé par travailleurs/essai_sentinel2.py --s3 (Sentinel 2, rasterio, cible s2-s3 de scripts/verifier_images.sh) et
tests/test_lectures_s3.py (lectures par la bibliothèque standard du serveur et du travailleur Sentinel 1) ; la lecture
de GDAL du travailleur Sentinel 1 a son propre faux S3, même contrôle (travailleurs/essai_fenetres.py, cible fenetres).
"""
import hashlib
import hmac
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def signature_valide(methode: str, chemin: str, entetes, cle: str, secret: str, region: str) -> str:
    """Contrôle de la signature d'une requête, comme S3 : '' si elle est valide, sinon le code d'erreur S3."""
    auth = entetes.get("Authorization", "")
    try:
        cred = auth.split("Credential=")[1].split(",")[0]
        signes = auth.split("SignedHeaders=")[1].split(",")[0]
        sig = auth.split("Signature=")[1].strip()
        cle_recue, jour, region_recue, service, _ = cred.split("/")
    except (IndexError, ValueError):
        return "AccessDenied"
    if cle_recue != cle:
        return "InvalidAccessKeyId"
    if region_recue != region or service != "s3":
        return "AuthorizationHeaderMalformed"
    u = urllib.parse.urlsplit(chemin)
    query = "&".join(sorted(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                            for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)))
    canon_h = "".join(f"{h}:{' '.join((entetes.get(h) or '').split())}\n" for h in signes.split(";"))
    canon = "\n".join([methode, urllib.parse.quote(urllib.parse.unquote(u.path), safe="/-_.~"), query, canon_h,
                       signes, entetes.get("x-amz-content-sha256", "")])
    scope = f"{jour}/{region}/s3/aws4_request"
    a_signer = "\n".join(["AWS4-HMAC-SHA256", entetes.get("x-amz-date", ""), scope,
                          hashlib.sha256(canon.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (jour, region, "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(k, a_signer.encode(), hashlib.sha256).hexdigest(), sig)
    return "" if ok else "SignatureDoesNotMatch"


class FauxS3:
    def __init__(self, cle: str, secret: str, region: str = "default", racine: str = "", miroir: str = ""):
        self.cle, self.secret, self.region, self.racine, self.miroir = cle, secret, region, racine, miroir
        self.envoyes = self.requetes = self.refus = 0
        self.lus: dict = {}                    # chemin -> octets envoyés
        self.verrou = threading.Lock()
        self.srv = None

    def demarrer(self) -> int:
        faux = self

        class Gestion(BaseHTTPRequestHandler):
            def do_GET(self):
                faux._servir(self, True)

            def do_HEAD(self):
                faux._servir(self, False)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Gestion)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self.srv.server_address[1]

    def arreter(self):
        if self.srv:
            self.srv.shutdown()

    @staticmethod
    def _erreur(h, code: int, s3: str, corps: bool):
        xml = f"<?xml version=\"1.0\"?><Error><Code>{s3}</Code><Message>{s3}</Message></Error>".encode()
        h.send_response(code)
        h.send_header("Content-Type", "application/xml")
        h.send_header("Content-Length", str(len(xml)))
        h.end_headers()
        if corps:
            h.wfile.write(xml)

    def _compter(self, chemin: str, n: int):
        with self.verrou:
            self.envoyes += n
            self.lus[chemin] = self.lus.get(chemin, 0) + n

    def _servir(self, h, corps: bool):
        with self.verrou:
            self.requetes += 1
        motif = signature_valide(h.command, h.path, h.headers, self.cle, self.secret, self.region)
        if motif:
            with self.verrou:
                self.refus += 1
            return self._erreur(h, 403, motif, corps)
        chemin = urllib.parse.unquote(urllib.parse.urlsplit(h.path).path)
        plage = h.headers.get("Range")
        if self.miroir:
            return self._relayer(h, chemin, plage, corps)
        fichier = os.path.join(self.racine, chemin.lstrip("/"))
        if not os.path.isfile(fichier):
            return self._erreur(h, 404, "NoSuchKey", corps)
        taille = os.path.getsize(fichier)
        a, b = 0, taille - 1
        if plage and plage.startswith("bytes="):
            x, _, y = plage[6:].split(",")[0].partition("-")
            a, b = int(x), min(int(y) if y else taille - 1, taille - 1)
        h.send_response(206 if plage else 200)
        h.send_header("Content-Length", str(b - a + 1))
        if plage:
            h.send_header("Content-Range", f"bytes {a}-{b}/{taille}")
        h.send_header("Accept-Ranges", "bytes")
        h.end_headers()
        if corps:
            with open(fichier, "rb") as f:
                f.seek(a)
                data = f.read(b - a + 1)
            self._compter(chemin, len(data))
            h.wfile.write(data)

    def _relayer(self, h, chemin: str, plage, corps: bool):
        # /eodata/<chemin> -> <miroir><chemin> : mêmes fichiers, sans clé, plage d'octets transmise
        if not chemin.startswith("/eodata/"):
            return self._erreur(h, 404, "NoSuchBucket", corps)
        req = urllib.request.Request(self.miroir + urllib.parse.quote(chemin[len("/eodata/"):], safe="/-_.~"),
                                     method="GET" if corps else "HEAD",
                                     headers={"Range": plage} if plage else {})
        try:
            r = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            return self._erreur(h, 404 if e.code == 404 else 502, "NoSuchKey" if e.code == 404 else "InternalError",
                                corps)
        with r:
            data = r.read() if corps else b""
            h.send_response(r.status)
            for k in ("Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
                if r.headers.get(k):
                    h.send_header(k, r.headers[k])
            h.end_headers()
            if corps:
                self._compter(chemin, len(data))
                h.wfile.write(data)
