from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = "PetinPetya + senamorsin"
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{BASE_DIR / 'instance' / 'site.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
