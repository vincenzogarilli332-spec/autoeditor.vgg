"""
auth.py

Autenticazione volutamente semplice: una sola password condivisa (sei
l'unico utente dell'app). La password si imposta con la variabile
d'ambiente APP_PASSWORD. Il frontend la richiede una volta e la
salva nel browser, mandandola poi in ogni richiesta come header
'X-App-Password'.
"""

import os

from fastapi import Header, HTTPException, Query


def require_password(x_app_password: str = Header(default=""), pw: str = Query(default="")):
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        return
    if x_app_password == expected or pw == expected:
        return
    raise HTTPException(status_code=401, detail="Password non valida")
