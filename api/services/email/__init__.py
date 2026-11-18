"""Outbound email: provider adapters and the sender/template/message models.

Bolo never sends from a shared platform account. Every organization connects
its own sender (SMTP, SES, Mailgun, or Mandrill) and its credentials are stored
through :mod:`api.services.credentials`.
"""
