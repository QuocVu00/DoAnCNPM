import mysql.connector
from mysql.connector import Error
from .config import Config
from backend.config import Config

def get_connection():
    """
    Tạo và trả về 1 connection tới MySQL.
    """
    return mysql.connector.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        database=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
    )

def query_one(sql, params=None):
    """
    Chạy SELECT trả về 1 dòng (hoặc None)
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, params or ())
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

def query_all(sql, params=None):
    """
    Chạy SELECT trả về danh sách nhiều dòng
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, params or ())
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def execute(sql, params=None):
    """
    Chạy INSERT / UPDATE / DELETE
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(sql, params or ())
    conn.commit()
    cursor.close()
    conn.close()
