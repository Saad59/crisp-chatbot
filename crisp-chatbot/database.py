from pymongo import MongoClient, errors
import os
from dotenv import load_dotenv
import atexit

load_dotenv()

class DatabaseManager:
    def __init__(self):
        self.client = None
        self.db = None
        self.msg_payloads_collection = None
        self._initialize_connection()

    def _initialize_connection(self):
        try:
            self.client = MongoClient(
                os.getenv("MONGO_URI"),
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=30000,
                socketTimeoutMS=30000
            )
            self.client.admin.command('ping')  # Test connection
            self.db = self.client.get_database("chat-bot")
            self.msg_payloads_collection = self.db["msg_payloads"]
            print("Successfully connected to MongoDB!")
        except Exception as e:
            print(f"Database connection failed: {str(e)}")
            raise

# Initialize connection
try:
    db_manager = DatabaseManager()
    msg_payloads_collection = db_manager.msg_payloads_collection
except Exception as e:
    print(f"Database initialization failed: {str(e)}")
    msg_payloads_collection = None