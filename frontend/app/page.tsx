"use client";

import { useState } from "react";
// Wir importieren Icons. Falls lucide-react fehlt, würde das hier crashen.
// Stell sicher, dass du 'npm install lucide-react' im frontend Ordner ausgeführt hast.
import { Search, Gauge, AlertTriangle, CheckCircle, ArrowRight, Globe } from "lucide-react";

// --- UI TEXTE ---
const UI_TEXTS = {
  de: {
    title: "Deal Anwalt",
    subtitle: "Zahle nie wieder zu viel für dein Traumauto.",
    placeholder: "Link von Mobile.de oder AutoScout24 einfügen...",
    button: "Kostenlos Checken",
    loading: "Analysiere Marktdaten & Ausstattung...",
    resultTitle: "Analyse Ergebnis",
    marketValue: "Marktwert Schätzung",
    actualPrice: "Aktueller Preis",
    savings: "Dein Verhandlungspotenzial",
    ammo: "🔥 Deine Munition:",
    script: "Sag genau das:",
    footer: "Keine Rechtsberatung. Nur für Bildungszwecke.",
    features: ["KI-Preisanalyse", "Ausstattungs-Check", "Verhandlungs-Skripte"]
  },
  en: {
    title: "Deal Lawyer",
    subtitle: "Never overpay for your dream car again.",
    placeholder: "Paste Mobile.de or AutoScout24 link...",
    button: "Check for Free",
    loading: "Analyzing market data & equipment...",
    resultTitle: "Analysis Result",
    marketValue: "Estimated Market Value",
    actualPrice: "Current Price",
    savings: "Negotiation Potential",
    ammo: "🔥 Your Ammo:",
    script: "Say exactly this:",
    footer: "No legal advice. Educational purposes only.",
    features: ["AI Price Analysis", "Equipment Check", "Negotiation Scripts"]
  }
};

export default function Home() {
  const [url, setUrl] = useState("");
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lang, setLang] = useState<"de" | "en">("de");

  // Safety Fallback für UI Texte
  const ui = UI_TEXTS[lang] || UI_TEXTS.de;
  
  // URL Fallback
  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

  // --- SICHERE ZAHLEN-PARSING FUNKTION ---
  // Diese Funktion stürzt nie ab, egal was reinkommt.
  const getSafeNumber = (val: any): number => {
    try {
        if (typeof val === 'number') return val;
        if (!val) return 0;
        
        if (typeof val === 'string') {
            // "25.000 €" -> "25000"
            const clean = val.replace(/\./g, '').replace(/,/g, '');
            // Suche erste Zahlengruppe
            const match = clean.match(/(\d{3,})/);
            if (match) {
                const num = parseInt(match[0]);
                // Sanity Check: Kein Auto kostet über 5 Mio (verhindert Parse-Fehler)
                if (num > 5000000) return 0;
                return num;
            }
        }
    } catch (e) {
        return 0; // Im Zweifel 0 zurückgeben
    }
    return 0;
  };

  const analyzeCar = async () => {
    const validDomains = ["mobile.de", "autoscout24", "kleinanzeigen", "ebay"];
    // Safety check falls url null ist
    const safeUrl = url || "";
    const isValid = validDomains.some(domain => safeUrl.toLowerCase().includes(domain));

    if (!isValid && safeUrl.length > 0) {
       setError(lang === "de" 
         ? "Bitte einen Link von Mobile.de, AutoScout24 oder Kleinanzeigen nutzen." 
         : "Please use a link from Mobile.de, AutoScout24, or Kleinanzeigen.");
       return;
    }
    
    setLoading(true);
    setError("");
    setReport(null);

    try {
      const res = await fetch(`${API_URL}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: safeUrl }), 
      });

      if (!res.ok) throw new Error("Server Error: " + res.statusText);
      const data = await res.json();
      setReport(data);
    } catch (err) {
      console.error(err); // Fehler in Konsole loggen
      setError(lang === "de" ? "Fehler bei der Analyse. Ist der Server online?" : "Analysis failed. Is the server running?");
    } finally {
      setLoading(false);
    }
  };

  // Sicheres Abrufen der Analysedaten
  const getAnalysisData = () => {
    if (!report || !report.analysis) return null;
    return report.analysis[lang] || report.analysis['de'] || report.analysis; 
  };

  const analysis = getAnalysisData();

  // --- BERECHNUNG (Sicher verpackt) ---
  let currentPrice = 0;
  let estimatedPrice = 0;
  let diff = 0;
  let displayEstimate = "---";

  // Wir berechnen das nur, wenn Daten da sind
  if (report && analysis) {
      try {
          currentPrice = getSafeNumber(report?.data?.price);
          estimatedPrice = getSafeNumber(analysis?.market_price_estimate);

          // Fallback Logik
          if (estimatedPrice < 100 || estimatedPrice > 5000000) {
              estimatedPrice = currentPrice;
          }

          diff = currentPrice - estimatedPrice;
          displayEstimate = estimatedPrice.toLocaleString();
      } catch (e) {
          console.error("Berechnungsfehler", e);
      }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans selection:bg-indigo-100">
      
      {/* HEADER */}
      <nav className="flex justify-between items-center p-6 max-w-5xl mx-auto">
        <div className="flex items-center gap-2 font-bold text-xl tracking-tight">
          <Gauge className="text-indigo-600" />
          <span>{ui?.title || "Deal Anwalt"}</span>
        </div>
        <button 
          onClick={() => setLang(lang === "de" ? "en" : "de")}
          className="flex items-center gap-1 text-sm font-medium text-slate-500 hover:text-indigo-600 transition bg-white px-3 py-1 rounded-full border border-slate-200 shadow-sm"
        >
          <Globe size={14} />
          {lang.toUpperCase()}
        </button>
      </nav>

      <main className="max-w-3xl mx-auto px-6 py-10 flex flex-col items-center">
        
        {/* HERO */}
        <div className="text-center mb-10 space-y-4">
          <h1 className="text-4xl md:text-5xl font-extrabold text-slate-900 tracking-tight leading-tight">
            {ui?.subtitle}
          </h1>
          <div className="flex flex-wrap justify-center gap-3 text-sm text-slate-600 pt-2">
            {ui?.features && ui.features.map((feat: string, i: number) => (
              <span key={i} className="bg-white border border-slate-200 px-3 py-1 rounded-full shadow-sm flex items-center gap-1">
                <CheckCircle size={14} className="text-green-500" /> {feat}
              </span>
            ))}
          </div>
        </div>

        {/* INPUT */}
        <div className="w-full bg-white p-2 rounded-2xl shadow-xl border border-slate-100 flex flex-col md:flex-row gap-2 transition-all hover:shadow-2xl hover:border-indigo-100">
          <div className="relative flex-grow">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={20} />
            <input
              type="text"
              placeholder={ui?.placeholder}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full pl-12 pr-4 py-4 rounded-xl outline-none text-lg text-slate-700 placeholder:text-slate-400"
            />
          </div>
          <button
            onClick={analyzeCar}
            disabled={loading}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-8 py-4 rounded-xl font-bold text-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 shadow-lg shadow-indigo-200"
          >
            {loading ? <span className="animate-spin">⏳</span> : <>{ui?.button} <ArrowRight size={20} /></>}
          </button>
        </div>

        {/* ERROR */}
        {error && (
          <div className="mt-6 p-4 bg-red-50 text-red-700 rounded-xl border border-red-100 flex items-center gap-3 w-full animate-in fade-in">
            <AlertTriangle /> {error}
          </div>
        )}

        {/* LOADING */}
        {loading && (
          <div className="mt-12 w-full space-y-4 animate-pulse">
            <div className="h-64 bg-slate-200 rounded-2xl w-full"></div>
            <div className="h-8 bg-slate-200 rounded w-2/3 mx-auto"></div>
          </div>
        )}

        {/* RESULTAT */}
        {report && !loading && analysis && (
          <div className="mt-12 w-full bg-white rounded-3xl shadow-2xl border border-slate-100 overflow-hidden animate-in zoom-in-95 duration-300">
            
            {/* Bild */}
            <div className="relative h-64 bg-slate-100">
              {report?.meta?.image ? (
                <img src={report.meta.image} alt="Car" className="w-full h-full object-cover" />
              ) : (
                <div className="flex items-center justify-center h-full text-slate-400">Kein Bild gefunden</div>
              )}
              <div className="absolute top-4 right-4">
                <span className={`px-4 py-2 rounded-full font-bold text-sm shadow-lg uppercase tracking-wider ${
                  (analysis?.rating || "").toLowerCase().includes("teuer") || (analysis?.rating || "").toLowerCase().includes("expensive") ? "bg-red-500 text-white" : 
                  (analysis?.rating || "").toLowerCase().includes("fair") ? "bg-yellow-400 text-slate-900" : "bg-green-500 text-white"
                }`}>
                  {analysis?.rating || "Info"}
                </span>
              </div>
            </div>

            <div className="p-8">
              <h2 className="text-2xl font-bold text-slate-900 mb-2">{report?.meta?.title || "Fahrzeug"}</h2>
              <div className="flex gap-4 text-sm text-slate-500 mb-6 font-medium">
                <span>🛣 {getSafeNumber(report?.data?.km).toLocaleString()} km</span>
                <span className="bg-slate-100 px-2 py-0.5 rounded text-slate-700">
                  Actual: {currentPrice.toLocaleString()} €
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
                <div className="p-5 bg-slate-50 rounded-2xl border border-slate-100 text-center">
                  <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">
                    {ui?.actualPrice}
                  </p>
                  <p className="text-3xl font-black text-slate-900">{currentPrice.toLocaleString()} €</p>
                </div>
                <div className="p-5 bg-indigo-50 rounded-2xl border border-indigo-100 text-center">
                  <p className="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-1">{ui?.marketValue}</p>
                  <p className="text-3xl font-black text-indigo-700">{displayEstimate} €</p>
                </div>
              </div>

              {/* NEGOTIATION BOX */}
              <div className="bg-gradient-to-br from-indigo-600 to-violet-700 text-white p-6 rounded-2xl shadow-lg relative overflow-hidden">
                <div className="relative z-10">
                  <p className="text-indigo-200 text-sm font-bold uppercase tracking-wider mb-1">{ui?.savings}</p>
                  
                  <p className="text-4xl font-extrabold mb-6">
                     {diff > 0 ? "-" : "+"}{Math.abs(diff).toLocaleString()} €
                  </p>
                  
                  <h3 className="font-bold text-white mb-3 flex items-center gap-2">
                    {ui?.ammo}
                  </h3>
                  <ul className="space-y-3 text-indigo-100 text-sm mb-6">
                    {Array.isArray(analysis?.arguments) && analysis.arguments.map((arg: string, index: number) => (
                      <li key={index} className="flex gap-2 items-start">
                         <span className="mt-1 bg-indigo-500/50 p-1 rounded-full text-[10px]">➤</span> {arg}
                      </li>
                    ))}
                  </ul>

                  <div className="bg-white/10 backdrop-blur-md p-4 rounded-xl border border-white/10">
                    <p className="text-[10px] font-bold text-indigo-200 uppercase mb-2">{ui?.script}</p>
                    <p className="italic text-white">"{analysis?.script || "..."}"</p>
                  </div>
                </div>
              </div>

            </div>
          </div>
        )}
      </main>

      <footer className="text-center p-8 text-slate-400 text-sm">
        <p>© {new Date().getFullYear()} {ui?.title}. {ui?.footer}</p>
      </footer>
    </div>
  );
}