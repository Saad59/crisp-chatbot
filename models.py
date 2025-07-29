from enum import Enum
from pydantic import BaseModel

class UserType(str, Enum):
    visitor = "visitor"
    customer = "customer"
    client = "client"

class UserFrom(str, Enum):
    chat = "chat"
    email = "email"

class MsgPayload(BaseModel):
    content: str
    type: str
    from_user: UserFrom
    user_type: UserType
