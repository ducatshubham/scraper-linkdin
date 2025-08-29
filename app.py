from flask import Flask, render_template, request
from scraper import run_scraper

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    results = None
    if request.method == "POST":
        company = request.form.get("company")
        role = request.form.get("role")
        limit = int(request.form.get("limit"))
        
        # ✅ Call scraper
        results = run_scraper(company, role, limit)
    return render_template("index.html", results=results)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
