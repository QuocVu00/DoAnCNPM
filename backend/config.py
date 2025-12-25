import os

class Config:
    # Thông tin DB trùng với Docker (chút nữa mình tạo)
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 3306))
    DB_NAME = os.getenv("DB_NAME", "smart_parking")
    DB_USER = os.getenv("DB_USER", "sp_user")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "sppassword")

    # secret cho Flask (tạm để vậy)
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
