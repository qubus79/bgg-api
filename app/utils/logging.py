# app/utils/logging.py

from colorama import Fore, Style
from datetime import datetime

def timestamp() -> str:
    return f"[{datetime.now():%Y-%m-%d %H:%M:%S}]"

def log_info(msg: str):
    print(Fore.CYAN + f"{timestamp()} [INFO] {msg}" + Style.RESET_ALL)

def log_success(msg: str):
    print(Fore.GREEN + f"{timestamp()} [SUCCESS] {msg}" + Style.RESET_ALL)

def log_warning(msg: str):
    print(Fore.YELLOW + f"{timestamp()} [WARNING] {msg}" + Style.RESET_ALL)

def log_error(msg: str):
    print(Fore.RED + f"{timestamp()} [ERROR] {msg}" + Style.RESET_ALL)
