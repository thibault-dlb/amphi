"""Thème sombre compact (fenêtre Windows classique)."""

from __future__ import annotations

COLORS = {
    "bg": "#1e1f22",
    "panel": "#26282c",
    "input": "#2d2f34",
    "border": "#3a3d42",
    "text": "#e4e4e6",
    "dim": "#9aa0a6",
    "rec": "#e5484d",
    "rec_hover": "#f2555a",
    "accent": "#3b82f6",
    "accent_hover": "#4f8ff7",
    "amber": "#f59e0b",
    "green": "#22c55e",
}

QSS = f"""
* {{
    font-family: "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 13px;
    color: {COLORS['text']};
}}
QMainWindow, QDialog {{ background: {COLORS['bg']}; }}
QMainWindow > QWidget {{ background: {COLORS['bg']}; }}

QMenuBar {{ background: {COLORS['bg']}; color: {COLORS['text']}; padding: 1px 2px; }}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {COLORS['input']}; }}
QMenu {{ background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {COLORS['accent']}; color: white; }}
QMenu::separator {{ height: 1px; background: {COLORS['border']}; margin: 4px 6px; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {COLORS['border']}; }}

QFrame#Card {{
    background: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
}}
QLabel#PaneHeader {{ color: {COLORS['dim']}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#Chrono {{ font-size: 30px; font-weight: 700; }}
QLabel#ChronoSub {{ color: {COLORS['dim']}; font-size: 11px; }}
QLabel#MicName {{ color: {COLORS['dim']}; font-size: 11px; }}
QLabel#Hint {{ color: {COLORS['dim']}; font-size: 11px; }}
QLabel#Banner {{ color: {COLORS['text']}; background: {COLORS['input']};
    border: 1px solid {COLORS['amber']}; border-radius: 8px; padding: 7px 10px; font-size: 12px; }}

QPushButton {{
    background: {COLORS['input']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 7px 12px;
}}
QPushButton:hover {{ border-color: {COLORS['accent']}; }}
QPushButton:disabled {{ color: #63666b; background: #24262a; }}

QPushButton#Record {{
    background: {COLORS['rec']}; border: none; color: white;
    font-size: 15px; font-weight: 700; padding: 13px; border-radius: 10px;
}}
QPushButton#Record:hover {{ background: {COLORS['rec_hover']}; }}
QPushButton#Record[recording="true"] {{ background: #3a2a2b; border: 1px solid {COLORS['rec']}; color: {COLORS['rec']}; }}

QPushButton#Primary {{ background: {COLORS['accent']}; border: none; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {COLORS['accent_hover']}; }}
QPushButton#Pause[paused="true"] {{ border-color: {COLORS['green']}; color: {COLORS['green']}; }}
QPushButton#Pause[paused="false"] {{ border-color: {COLORS['amber']}; color: {COLORS['amber']}; }}

QPushButton#IconBtn {{ background: transparent; border: none; padding: 4px 8px; color: {COLORS['dim']}; font-size: 14px; }}
QPushButton#IconBtn:hover {{ color: {COLORS['text']}; }}
QPushButton#IconBtn[active="true"] {{ color: {COLORS['accent']}; }}

QComboBox, QLineEdit, QDateEdit, QPlainTextEdit, QSpinBox {{
    background: {COLORS['input']}; border: 1px solid {COLORS['border']};
    border-radius: 7px; padding: 5px 8px; selection-background-color: {COLORS['accent']};
}}
QComboBox:focus, QLineEdit:focus, QDateEdit:focus, QPlainTextEdit:focus {{ border-color: {COLORS['accent']}; }}
QComboBox QAbstractItemView {{ background: {COLORS['input']}; border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['accent']}; }}

QListWidget {{ background: {COLORS['bg']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}
QListWidget::item {{ padding: 2px; }}
QListWidget::item:selected {{ background: {COLORS['input']}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {COLORS['border']}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QProgressBar {{ background: {COLORS['input']}; border: none; border-radius: 4px; height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {COLORS['accent']}; border-radius: 4px; }}

QToolTip {{ background: {COLORS['panel']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; }}
QCheckBox {{ spacing: 7px; }}
QTabWidget::pane {{ border: 1px solid {COLORS['border']}; border-radius: 8px; }}
QTabBar::tab {{ background: transparent; padding: 6px 12px; color: {COLORS['dim']}; }}
QTabBar::tab:selected {{ color: {COLORS['text']}; border-bottom: 2px solid {COLORS['accent']}; }}
"""
