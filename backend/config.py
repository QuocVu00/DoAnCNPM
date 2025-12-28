import os


class Config:
    """
    Cấu hình dùng chung cho Flask app:
    - Kết nối MySQL chạy trong Docker (db service)
    - SECRET_KEY cho session Flask
    """

    # KẾT NỐI DATABASE
    # MySQL trong docker đã được map port 3306 ra máy thật:
    #   ports:
    #     - "3306:3306"
    #
    # Nên từ Windows (Flask chạy bằng `python app.py`) chỉ cần:
    #   host = 127.0.0.1
    #   port = 3306
    #   user = sp_user
    #   password = sppassword
    #   database = smart_parking

    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("DB_PORT", 3306))
    DB_NAME = os.getenv("DB_NAME", "smart_parking")
    DB_USER = os.getenv("DB_USER", "sp_user")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "sppassword")

    # SECRET_KEY cho Flask (dùng cho session, flash message, v.v.)
    # Khi deploy thật thì nên đổi sang chuỗi random dài, hoặc dùng biến môi trường.
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
