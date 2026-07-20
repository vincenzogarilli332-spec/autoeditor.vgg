"""
auth.py

Autenticazione volutamente semplice: una sola password condivisa (sei
l'unico utente dell'app). La password si imposta con la variabile
d'ambiente APP_PASSWORD. Il frontend la richiede una volta e la
salva nel browser, mandandola poi in ogni richiesta come header
'X-App-Password'.
"""

import os

from fastapi import Header, HTTPException


def require_password(x_app_password: str = Header(default="")):
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        # Se non e' stata impostata nessuna password, l'app resta aperta
        # (utile in locale durante lo sviluppo) ma segnaliamolo in log.
        return
    if x_app_password != expected:
        raise HTTPException(status_code=401, detail="Password non valida")
