from flask import Flask, jsonify
from config import Config

from routes_gate import gate_bp
from routes_admin import admin_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    app.register_blueprint(gate_bp)
    app.register_blueprint(admin_bp)

    @app.route("/")
    def index():
        return jsonify({"message": "Smart Parking Backend is running"})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
