"""First-use notice for opening WhatsApp click-to-chat links.

The operator types and sends the message inside their own WhatsApp account, so
the risk of a number being restricted for unsolicited messages is theirs. Bolo
records that they saw this notice once per user and organization, and refuses to
build click-to-chat links until then. Bumping ``WHATSAPP_DISCLAIMER_VERSION``
asks everyone again.
"""

WHATSAPP_DISCLAIMER_VERSION = "2026-11-20"

WHATSAPP_DISCLAIMER_TEXT = (
    "WhatsApp can ban or restrict a number that sends spam or messages people "
    "did not ask for. BoloAgent is not responsible for anything WhatsApp does to "
    "your account or number. Only message people who agreed to hear from you, "
    "and send responsibly."
)
