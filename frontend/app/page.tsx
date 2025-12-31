"use client";
import { useState } from "react";

export default function Home() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<any>(null);
  const [error, setError] = useState("");

  const analyzeCar = async () => {
    if (!url) return;
    setLoading(true);
    setError("");
    setReport(null);

    try {
      // Wir rufen DEIN Backend auf
      const res = await fetch("http://127.0.0.1:8000/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      if (!res.ok) throw new Error("Fehler beim Abruf. Link prüfen!");
      
      const data = await res.json();
      setReport(data);
    } catch (err: any) {
      setError(err.message || "Etwas ist schiefgelaufen.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-gray-50 flex flex-col items-center py-12 px-4">
      {/* HEADER */}
      <div className="text-center mb-10">
        <h1 className="text-4xl font-extrabold text-gray-900 mb-2">
          Der Deal Anwalt ⚖️
        </h1>
        <p className="text-gray-600">
          Zahle nie wieder zu viel. Wir checken dein Mobile.de Inserat.
        </p>
      </div>

      {/* INPUT BEREICH */}
      <div className="w-full max-w-xl bg-white p-6 rounded-xl shadow-lg">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Hier Mobile.de Link einfügen..."
            className="flex-1 border border-gray-300 rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-blue-500 text-black"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <button
            onClick={analyzeCar}
            disabled={loading}
            className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-6 rounded-lg transition-colors disabled:opacity-50"
          >
            {loading ? "Analysiere..." : "Checken"}
          </button>
        </div>
        
        {error && <p className="text-red-500 mt-3 text-sm">{error}</p>}
      </div>

      {/* ERGEBNIS REPORT */}
      {report && (
        <div className="w-full max-w-xl mt-8 animate-fade-in">
          {/* HEADER CARD */}
          <div className="bg-white rounded-t-xl overflow-hidden shadow-sm border-b">
            {report.meta.image && (
              <img src={report.meta.image} alt="Car" className="w-full h-48 object-cover" />
            )}
            <div className="p-6">
              <h2 className="text-2xl font-bold text-gray-800">{report.meta.title}</h2>
              <div className="flex gap-4 mt-2 text-sm text-gray-500">
                <span>📍 {report.data.ez}</span>
                <span>🛣️ {report.data.km.toLocaleString()} km</span>
                <span>⚡ {report.data.power}</span>
              </div>
            </div>
          </div>

          {/* ANALYSIS CARD */}
          {/* --- NEU: KI ARGUMENTE --- */}
          {report.analysis.arguments && (
                <div className="mt-6">
                  <h3 className="font-bold text-gray-800 mb-2">🔥 Deine Munition:</h3>
                  <ul className="list-disc pl-5 space-y-2 text-sm text-gray-700">
                    {report.analysis.arguments.map((arg: string, index: number) => (
                      <li key={index}>{arg}</li>
                    ))}
                  </ul>
                  
                  <div className="mt-4 bg-blue-50 p-3 rounded border border-blue-100">
                    <p className="text-xs font-bold text-blue-800 uppercase mb-1">Sag genau das:</p>
                    <p className="text-blue-900 italic">"{report.analysis.script}"</p>
                  </div>
                </div>
              )}
          <div className="bg-white rounded-b-xl p-6 shadow-lg border-t-0">
            <div className="flex justify-between items-center mb-6">
              <div>
                <p className="text-sm text-gray-500 uppercase font-semibold">Aktueller Preis</p>
                <p className="text-3xl font-bold text-gray-900">
                  {report.data.price.toLocaleString()} €
                </p>
              </div>
              
              {/* AMPEL LOGIK */}
              <div className={`px-4 py-2 rounded-full font-bold text-white 
                ${report.analysis.rating === 'teuer' ? 'bg-red-500' : 'bg-green-500'}`}>
                {report.analysis.rating === 'teuer' ? 'ZU TEUER 👎' : 'GUTER DEAL 👍'}
              </div>
            </div>

            <div className="bg-gray-50 p-4 rounded-lg border border-gray-200">
              <p className="text-sm text-gray-600 mb-1">Marktwert Schätzung:</p>
              <p className="font-semibold text-gray-800">
                ca. {report.analysis.market_price_estimate.toLocaleString()} €
              </p>
              
              {report.analysis.negotiation_potential > 0 && (
                <div className="mt-3 pt-3 border-t border-gray-200">
                  <p className="text-green-600 font-bold">
                    💰 Dein Verhandlungspotenzial: -{report.analysis.negotiation_potential.toLocaleString()} €
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    Nutze Argumente wie Standzeit und Reifen, um diesen Preis zu erreichen.
                  </p>
                </div>
              )}
            </div>
            
            <p className="text-xs text-gray-300 mt-6 text-center">
              *Alle Angaben ohne Gewähr. Keine Rechtsberatung.
            </p>
          </div>
        </div>
      )}
    </main>
  );
}