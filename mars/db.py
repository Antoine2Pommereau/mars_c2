import psycopg

from mars.config import database_url


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())
