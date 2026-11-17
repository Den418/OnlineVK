#!/usr/bin/env python3
"""Запуск бота: python online_tracker.py (то же самое, что python -m tracker).

Вся логика лежит в папке tracker/, этот файл только запускает её,
поэтому он должен оставаться рядом с этой папкой.
"""
from tracker.__main__ import run

if __name__ == "__main__":
    run()
