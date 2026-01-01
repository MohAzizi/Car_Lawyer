"use client";

import { useState } from "react";
import { Search, Gauge, AlertTriangle, CheckCircle, ArrowRight, Globe } from "lucide-react";

// --- TEXTE FÜR DE/EN (Einfache Lösung) ---
const TRANSLATIONS = {
  de: {
    title: "Deal Anwalt",
    subtitle: "Zahle nie wieder zu viel für dein Traumauto.",
    placeholder: "Mobile.de Link hier einfügen...",
    button: "Kostenlos Checken",
    loading: "Analysiere Marktdaten...",
    resultTitle: "Analyse Ergebnis",
    marketValue: "Marktwert Schätzung",
    savings: "Dein Verhandlungspotenzial",
    rating: {
      bad: "ZU TEUER",
      fair: "FAIRER PREIS",
      good: "TOP DEAL"
    },
    ammo: "🔥 Deine Munition:",
    script: "Sag genau das:",
    footer: "Keine Rechtsberatung. Nur für Bildungszwecke.",
    features: ["KI-Preisanalyse", "Verhandlungs-Skripte", "Versteckte Mängel finden"]
  },
  en: {
    title: "Deal Lawyer",
    subtitle: "Never overpay for your dream car again.",
    placeholder: "Paste Mobile.de link here...",
    button: "Check for Free",
    loading: "Analyzing market data...",
    resultTitle: "Analysis Result",
    marketValue: "Estimated Market Value",
    savings: "Negotiation Potential",
    rating: {
      bad: "TOO EXPENSIVE",
      fair: "FAIR PRICE",
      good: "GREAT DEAL"
    },
    ammo: "🔥 Your Ammo:",
    script: "Say exactly this:",
    footer: "No legal advice. Educational purposes only.",
    features: ["AI Price Analysis", "Negotiation Scripts", "Find Hidden Flaws"]
  }
};

export default function Home() {
  const [url, setUrl] = useState("");
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lang, setLang] = useState<"de" | "en">("de"); // Sprache

  const t = TRANSLATIONS[lang]; // Aktuelle Texte
  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

  const analyzeCar = async () => {
    if (!url.includes("mobile.de")) {
      setError(lang === "de" ? "Bitte einen gültigen Mobile.de Link eingeben." : "Please enter a valid Mobile.de link.");
      return;
    }
    
    setLoading(true);
    setError("");
    setReport(null);

    try {
      const res = await fetch(`${API_URL}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      if (!res.ok) throw new Error("Server Error");
      const data = await res.json();
      setReport(data);
    } catch (err) {
      setError(lang === "de" ? "Fehler bei der Analyse. Ist der Link korrekt?" : "Analysis failed. Is the link correct?");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 font-sans selection:bg-indigo-100">
      
      {/* HEADER */}
      <nav className="flex justify-between items-center p-6 max-w-5xl mx-auto">
        <div className="flex items-center gap-2 font-bold text-xl tracking-tight">
          <Gauge className="text-indigo-600" />
          <span>{t.title}</span>
        </div>
        <button 
          onClick={() => setLang(lang === "de" ? "en" : "de")}
          className="flex items-center gap-1 text-sm font-medium text-slate-500 hover:text-indigo-600 transition"
        >
          <Globe size={16} />
          {lang.toUpperCase()}
        </button>
      </nav>

      <main className="max-w-3xl mx-auto px-6 py-10 flex flex-col items-center">
        
        {/* HERO SECTION */}
        <div className="text-center mb-10 space-y-4">
          <h1 className="text-4xl md:text-5xl font-extrabold text-slate-900 tracking-tight leading-tight">
            {t.subtitle}
          </h1>
          
          {/* Feature Badges */}
          <div className="flex flex-wrap justify-center gap-3 text-sm text-slate-600 pt-2">
            {t.features.map((feat, i) => (
              <span key={i} className="bg-white border border-slate-200 px-3 py-1 rounded-full shadow-sm flex items-center gap-1">
                <CheckCircle size={14} className="text-green-500" /> {feat}
              </span>
            ))}
          </div>
        </div>

        {/* INPUT CARD */}
        <div className="w-full bg-white p-2 rounded-2xl shadow-xl border border-slate-100 flex flex-col md:flex-row gap-2 transition-all hover:shadow-2xl hover:border-indigo-100">
          <div className="relative flex-grow">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" size={20} />
            <input
              type="text"
              placeholder={t.placeholder}
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
            {loading ? (
              <span className="animate-spin">⏳</span> 
            ) : (
              <>
                {t.button} <ArrowRight size={20} />
              </>
            )}
          </button>
        </div>

        {/* ERROR MESSAGE */}
        {error && (
          <div className="mt-6 p-4 bg-red-50 text-red-700 rounded-xl border border-red-100 flex items-center gap-3 w-full animate-in fade-in slide-in-from-bottom-4">
            <AlertTriangle /> {error}
          </div>
        )}

        {/* LOADING STATE (SKELETON) */}
        {loading && (
          <div className="mt-12 w-full space-y-4 animate-pulse">
            <div className="h-64 bg-slate-200 rounded-2xl w-full"></div>
            <div className="h-8 bg-slate-200 rounded w-2/3 mx-auto"></div>
          </div>
        )}

        {/* RESULT CARD */}
        {report && !loading && (
          <div className="mt-12 w-full bg-white rounded-3xl shadow-2xl border border-slate-100 overflow-hidden animate-in zoom-in-95 duration-300">
            
            {/* Bild & Header */}
            <div className="relative h-64 bg-slate-100">
              {report.meta.image ? (
                <img src={report.meta.image} alt="Auto" className="w-full h-full object-cover" />
              ) : (
                <div className="flex items-center justify-center h-full text-slate-400">Kein Bild</div>
              )}
              <div className="absolute top-4 right-4">
                <span className={`px-4 py-2 rounded-full font-bold text-sm shadow-lg uppercase tracking-wider ${
                  report.analysis.rating.toLowerCase().includes("teuer") || report.analysis.rating.toLowerCase().includes("expensive") ? "bg-red-500 text-white" : 
                  report.analysis.rating.toLowerCase().includes("fair") ? "bg-yellow-400 text-slate-900" : "bg-green-500 text-white"
                }`}>
                  {report.analysis.rating}
                </span>
              </div>
            </div>

            <div className="p-8">
              <h2 className="text-2xl font-bold text-slate-900 mb-2">{report.meta.title}</h2>
              <div className="flex gap-4 text-sm text-slate-500 mb-6 font-medium">
                <span>🗓 {report.data.ez}</span>
                <span>🛣 {report.data.km.toLocaleString()} km</span>
                <span>⚡ {report.data.power}</span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
                <div className="p-5 bg-slate-50 rounded-2xl border border-slate-100">
                  <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Aktueller Preis</p>
                  <p className="text-3xl font-black text-slate-900">{report.data.price.toLocaleString()} €</p>
                </div>
                <div className="p-5 bg-indigo-50 rounded-2xl border border-indigo-100">
                  <p className="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-1">{t.marketValue}</p>
                  <p className="text-3xl font-black text-indigo-700">{report.analysis.market_price_estimate.toLocaleString()} €</p>
                </div>
              </div>

              {/* NEGOTIATION BOX */}
              <div className="bg-gradient-to-br from-indigo-600 to-violet-700 text-white p-6 rounded-2xl shadow-lg relative overflow-hidden">
                <div className="relative z-10">
                  <p className="text-indigo-200 text-sm font-bold uppercase tracking-wider mb-1">{t.savings}</p>
                  <p className="text-4xl font-extrabold mb-6">
                    {report.analysis.negotiation_potential > 0 ? "-" : "+"}{Math.abs(report.analysis.negotiation_potential).toLocaleString()} €
                  </p>

                  <h3 className="font-bold text-white mb-3 flex items-center gap-2">
                    {t.ammo}
                  </h3>
                  <ul className="space-y-3 text-indigo-100 text-sm mb-6">
                    {report.analysis.arguments?.map((arg: string, index: number) => (
                      <li key={index} className="flex gap-2 items-start">
                         <span className="mt-1 bg-indigo-500/50 p-1 rounded-full text-[10px]">➤</span> {arg}
                      </li>
                    ))}
                  </ul>

                  <div className="bg-white/10 backdrop-blur-md p-4 rounded-xl border border-white/10">
                    <p className="text-[10px] font-bold text-indigo-200 uppercase mb-2">{t.script}</p>
                    <p className="italic text-white">"{report.analysis.script}"</p>
                  </div>
                </div>
              </div>

            </div>
          </div>
        )}
      </main>

      {/* FOOTER */}
      <footer className="text-center p-8 text-slate-400 text-sm">
        <p>© {new Date().getFullYear()} {t.title}. {t.footer}</p>
      </footer>
    </div>
  );
}