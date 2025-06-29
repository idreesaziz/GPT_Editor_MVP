from pydantic import BaseModel

class EditRequest(BaseModel):
    session_id: str
    prompt: str

class UndoRequest(BaseModel):
    session_id: str
    steps: int = 1
