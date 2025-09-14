import mysql.connector
import os
from typing import Optional

class RestrictionDB:
    def __init__(self):
        self.host = os.getenv("DB_HOST", "")
        self.port = int(os.getenv("DB_PORT", ""))
        self.user = os.getenv("DB_USER", "")
        self.password = os.getenv("DB_PASSWORD", "")
        self.database = os.getenv("DB_NAME", "")
        self._init_db()
    
    def _get_connection(self):
        return mysql.connector.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database
        )
    
    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voice_restrictions (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL
            )
        """)
        conn.commit()
        cursor.close()
        conn.close()
    
    def set_restriction(self, guild_id: int, channel_id: int):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO voice_restrictions (guild_id, channel_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE channel_id = %s
        """, (guild_id, channel_id, channel_id))
        conn.commit()
        cursor.close()
        conn.close()
    
    def get_restriction(self, guild_id: int) -> Optional[int]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT channel_id FROM voice_restrictions WHERE guild_id = %s
        """, (guild_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result else None
    
    def remove_restriction(self, guild_id: int):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM voice_restrictions WHERE guild_id = %s
        """, (guild_id,))
        conn.commit()
        cursor.close()
        conn.close()
    
    def has_restriction(self, guild_id: int) -> bool:
        return self.get_restriction(guild_id) is not None
