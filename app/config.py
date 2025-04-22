# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    ZNADPLANSZY_URL: str = "https://premiery.znadplanszy.pl/catalogue"

settings = Settings()
