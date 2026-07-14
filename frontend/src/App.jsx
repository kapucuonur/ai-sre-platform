import React, { useState } from 'react'
import { LayoutDashboard, AlertTriangle, Sliders, Shield, Database, History as HistoryIcon, Key } from 'lucide-react'
import Dashboard from './components/Dashboard'
import Incidents from './components/Incidents'
import Settings from './components/Settings'
import History from './components/History'
import Registry from './components/Registry'
import PaymentSuccess from './components/PaymentSuccess'

// --- Global Fetch Interceptor for Multi-Tenancy ---
const originalFetch = window.fetch;
window.fetch = function (url, options = {}) {
  const apiKey = localStorage.getItem('sre_api_key') || '';
  if (apiKey) {
    options.headers = {
      ...options.headers,
      'X-SRE-API-Key': apiKey
    };
  }
  return originalFetch(url, options);
};

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [apiKey, setApiKey] = useState(localStorage.getItem('sre_api_key') || '')
  const [apiInput, setApiInput] = useState('')
  const [loginError, setLoginError] = useState('')

  // Check if we are on the Stripe payment success page
  const urlParams = new URLSearchParams(window.location.search)
  const sessionId = urlParams.get('session_id')

  if (sessionId) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', width: '100vw' }}>
        <PaymentSuccess />
      </div>
    )
  }

  const handleLogin = (e) => {
    e.preventDefault()
    if (!apiInput.trim()) {
      setLoginError('Please enter an API Key.')
      return
    }
    // Simple validation (can be bypassed locally, backend secures it)
    localStorage.setItem('sre_api_key', apiInput.trim())
    setApiKey(apiInput.trim())
  }

  const handleLocalMode = () => {
    localStorage.setItem('sre_api_key', 'self-hosted')
    setApiKey('self-hosted')
  }

  const handleLogout = () => {
    localStorage.removeItem('sre_api_key')
    setApiKey('')
  }

  if (!apiKey) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        width: '100vw',
        background: '#060a0f',
        color: '#e2e8f0',
        fontFamily: 'system-ui, sans-serif'
      }}>
        <div style={{
          background: '#0b131c',
          border: '1px solid rgba(255, 255, 255, 0.08)',
          borderRadius: '16px',
          padding: '40px',
          width: '90%',
          maxWidth: '400px',
          boxShadow: '0 20px 40px rgba(0,0,0,0.5)'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '24px', justifyContent: 'center' }}>
            <Shield size={28} style={{ color: '#22d3ee' }} />
            <h2 style={{ fontSize: '20px', fontWeight: 700, margin: 0 }}>SRE<span>daemon</span></h2>
          </div>
          
          <h3 style={{ fontSize: '16px', margin: '0 0 8px 0', textAlign: 'center' }}>Access Platform Dashboard</h3>
          <p style={{ fontSize: '13px', color: '#94a3b8', margin: '0 0 24px 0', textAlign: 'center' }}>
            Enter your client API Key received via email.
          </p>

          <form onSubmit={handleLogin}>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px', fontWeight: 600 }}>API KEY</label>
              <div style={{ position: 'relative' }}>
                <input 
                  type="password" 
                  placeholder="sre_live_..."
                  value={apiInput}
                  onChange={(e) => setApiInput(e.target.value)}
                  style={{
                    width: '100%',
                    boxSizing: 'border-box',
                    background: '#0e1924',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: '8px',
                    padding: '12px 16px',
                    color: '#fff',
                    outline: 'none',
                    fontSize: '14px'
                  }}
                />
              </div>
            </div>

            {loginError && <p style={{ color: '#f87171', fontSize: '12px', marginTop: '-8px', marginBottom: '12px' }}>{loginError}</p>}

            <button 
              type="submit"
              style={{
                width: '100%',
                background: '#22d3ee',
                color: '#060a0f',
                border: 'none',
                borderRadius: '8px',
                padding: '12px',
                fontSize: '14px',
                fontWeight: 700,
                cursor: 'pointer',
                marginBottom: '12px',
                transition: 'opacity 0.2s'
              }}
            >
              Connect Dashboard
            </button>
          </form>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: '16px 0', color: '#475569' }}>
            <hr style={{ flex: 1, border: 'none', height: '1px', background: 'rgba(255,255,255,0.08)' }} />
            <span style={{ fontSize: '12px' }}>or</span>
            <hr style={{ flex: 1, border: 'none', height: '1px', background: 'rgba(255,255,255,0.08)' }} />
          </div>

          <button 
            onClick={handleLocalMode}
            style={{
              width: '100%',
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.08)',
              color: '#f8fafc',
              borderRadius: '8px',
              padding: '12px',
              fontSize: '13px',
              fontWeight: 600,
              cursor: 'pointer'
            }}
          >
            Use Self-Hosted (Free)
          </button>
        </div>
      </div>
    )
  }

  const renderContent = () => {
    switch (activeTab) {
      case 'dashboard':
        return <Dashboard />
      case 'incidents':
        return <Incidents />
      case 'history':
        return <History />
      case 'registry':
        return <Registry />
      case 'settings':
        return <Settings />
      default:
        return <Dashboard />
    }
  }

  return (
    <div className="app-container">
      {/* Sidebar Navigation */}
      <aside className="sidebar">
        <div className="brand-logo">
          <Shield size={24} className="brand-icon" />
          <h2>AI SRE<span> Platform</span></h2>
        </div>

        <nav>
          <ul className="nav-links">
            <li className="nav-item">
              <button 
                onClick={() => setActiveTab('dashboard')} 
                className={`nav-button ${activeTab === 'dashboard' ? 'active' : ''}`}
              >
                <LayoutDashboard size={18} />
                Dashboard
              </button>
            </li>
            <li className="nav-item">
              <button 
                onClick={() => setActiveTab('incidents')} 
                className={`nav-button ${activeTab === 'incidents' ? 'active' : ''}`}
              >
                <AlertTriangle size={18} />
                Incidents
              </button>
            </li>
            <li className="nav-item">
              <button 
                onClick={() => setActiveTab('history')} 
                className={`nav-button ${activeTab === 'history' ? 'active' : ''}`}
              >
                <HistoryIcon size={18} />
                History
              </button>
            </li>
            <li className="nav-item">
              <button 
                onClick={() => setActiveTab('registry')} 
                className={`nav-button ${activeTab === 'registry' ? 'active' : ''}`}
              >
                <Database size={18} />
                Registry
              </button>
            </li>
            <li className="nav-item">
              <button 
                onClick={() => setActiveTab('settings')} 
                className={`nav-button ${activeTab === 'settings' ? 'active' : ''}`}
              >
                <Sliders size={18} />
                Settings
              </button>
            </li>
            <li className="nav-item" style={{ marginTop: '20px', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '15px' }}>
              <a 
                href="/" 
                className="nav-button"
                style={{ textDecoration: 'none', color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '8px' }}
              >
                <span>←</span> Back to Home
              </a>
            </li>
            <li className="nav-item">
              <button 
                onClick={handleLogout}
                className="nav-button"
                style={{ color: '#ef4444', display: 'flex', alignItems: 'center', gap: '8px', background: 'none', border: 'none', width: '100%', cursor: 'pointer', textAlign: 'left' }}
              >
                <span>🚪</span> Logout
              </button>
            </li>
          </ul>
        </nav>
      </aside>

      {/* Main Panel Content */}
      <main className="main-content">
        {renderContent()}
      </main>
    </div>
  )
}

export default App
