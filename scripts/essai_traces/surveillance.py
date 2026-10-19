"""Essai des traces de bout en bout, côté serveur : une minute de surveillance (mars/travailleurs.supervise) traite
les résultats reçus et détruit l'instance (Scaleway simulé) ; ces spans rejoignent la trace du lancement."""
import os

import psycopg

from mars import sentinel, traces, travailleurs
from mars.config import load_rules


class ScalewaySimule:
    project, protected = None, set()

    def destroy(self, *_a, **_k):
        return True

    def tagged(self, _zone):
        return []


traces.init("mars-taches")
rules = load_rules()
with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    with traces.span("taches.minute", nouvelle_trace=True):
        out = travailleurs.supervise(c, ScalewaySimule(), rules, {"sentinel": lambda c, w: sentinel.ingest(c, w, rules)})
traces.fermer()
print(out)
