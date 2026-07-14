import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
try:
    client = OpenAI(
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL")
    )
    completion = client.chat.completions.create(
        model=os.getenv("MODEL_NAME"),
        messages=[
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': '你是谁？'}
        ]
    )
    print(completion)
except Exception as e:
    print(f"错误信息：{e}")
