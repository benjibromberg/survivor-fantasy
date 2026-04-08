import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app

app = create_app()

if __name__ == "__main__":
    # host 0.0.0.0 makes it accessible on your Tailnet
    app.run(host="0.0.0.0", port=5050, debug=os.environ.get("FLASK_DEBUG", "1") == "1")
