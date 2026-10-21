"""Current Telegram administrator capabilities; no delayed permission cache."""


def can_moderate(member: object, flow: str) -> bool:
    """Rights belong to the invoking admin, not merely to the bot.

    Owners always have the corresponding right. Unknown statuses fail closed.
    """
    status = getattr(member, "status", None)
    if status in ("creator", "owner"):
        return True
    if status != "administrator":
        return False
    permission = {
        "member": "can_restrict_members",
        "request": "can_invite_users",
    }.get(flow)
    return bool(permission and getattr(member, permission, False) is True)
