"""Dansk kvindebiografisk leksikon (P7939) on kvindebiografiskleksikon.lex.dk: the same JSON metadata as Lex (lex.py)."""
from readers3 import lex
import sources3 as s

WORK, HOST = "dkl", "kvindebiografiskleksikon.lex.dk"


def candidates(n):
    return s.linked("P7939", n)


def read(slug):
    return lex.read(slug, WORK, HOST)
