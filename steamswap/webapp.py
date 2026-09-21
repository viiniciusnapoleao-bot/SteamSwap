"""Ponto de entrada da interface: pywebview mostrando a página em steamswap/webui,
com a lógica (steam.py, swap.py, stub.py, compat.py, shortcuts.py) exposta via
webapi.Api. A lógica de negócio não muda entre esta interface e a antiga em
Tkinter — só a camada de apresentação."""
from pathlib import Path

import webview

from .webapi import Api

UI_DIR = Path(__file__).parent / "webui"


def main():
    api = Api()
    window = webview.create_window(
        "SteamSwap", url=str(UI_DIR / "index.html"), js_api=api,
        width=1080, height=720, min_size=(900, 600),
        background_color="#171a21",
    )
    api.bind(window)
    webview.start()


if __name__ == "__main__":
    main()
