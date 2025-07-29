from fastapi.testclient import TestClient
from main import app
import pytest

client = TestClient(app)

def test_chat_endpoint():
    # Skip test if no database connection
    from database import msg_payloads_collection
    if msg_payloads_collection is None:
        pytest.skip("No database connection available")
    
    payload = {
        "content": "Hi there!",
        "type": "text",
        "from_user": "chat",
        "user_type": "customer"
    }
    
    response = client.post("/chat", json=payload)
    
    assert response.status_code == 200
    assert "reply" in response.json()
    
    # Verify data was saved
    saved = msg_payloads_collection.find_one({"content": "Hi there!"})
    assert saved is not None
    assert saved["type"] == "text"
    assert saved["from_user"] == "chat"