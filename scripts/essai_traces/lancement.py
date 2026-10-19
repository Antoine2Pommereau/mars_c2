"""Essai des traces de bout en bout (scripts/verifier_images.sh traces), côté serveur : lancement simulé d'un
travailleur Sentinel 1 dans une tâche tracée, comme mars/travailleurs.launch (sans Scaleway) ; écrit la ligne du
travailleur avec son jeton (empreinte) et son contexte de trace. Affiche : identifiant, traceparent."""
import hashlib
import json
import os

import psycopg

from mars import traces

traces.init("mars-taches")
jeton = os.environ["JETON"]
with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    with traces.span("tache sentinel", nouvelle_trace=True, **{"tache.nom": "sentinel"}):
        with traces.span("sentinel.selection", **{"sentinel.passages": 1}):
            pass
        with traces.span("travailleur.lancement", **{"travailleur.tache": "sentinel"}):
            wid = c.execute(
                "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou, etat, "
                "scw_server_id, trace_id, traceparent) VALUES ('sentinel', 'ESSAI', 'fr-par-2', %s, %s, "
                "'essai_traces', 'cree', 'essai', %s, %s) RETURNING id",
                (hashlib.sha256(jeton.encode()).hexdigest(), json.dumps({"scenes": [], "regions": []}),
                 traces.trace_id(), traces.traceparent())).fetchone()[0]
            tp = traces.traceparent()
traces.fermer()
print(wid, tp)
