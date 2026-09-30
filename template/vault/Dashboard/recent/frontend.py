"""How the `recent` card is drawn: a plain list, from the dashboard's own pieces."""


def render(data, ctx, ui):
    return ui.items(data['items'], empty='No notes yet.')
