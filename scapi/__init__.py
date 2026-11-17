"""Restricted StarCraft single-player API. Importing does not attach to a game.

Original implementation copied for standalone distribution on 2026-11-16.
Stage5 is the current entry point; Actions remains the legacy base interface.
"""
from .actions import Actions, SelectionTicket
from .stage5 import Stage5, ButtonTicket, TargetTicket, ProductionTicket, CargoTicket

__all__ = ['Stage5', 'Actions', 'SelectionTicket', 'ButtonTicket', 'TargetTicket',
           'ProductionTicket', 'CargoTicket']
