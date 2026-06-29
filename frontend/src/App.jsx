import React, { useState } from 'react'
import { LayoutDashboard, AlertTriangle, Sliders, Shield, Database, History as HistoryIcon } from 'lucide-react'
import Dashboard from './components/Dashboard'
import Incidents from './components/Incidents'
import Settings from './components/Settings'
import History from './components/History'
import Registry from './components/Registry'
import PaymentSuccess from './components/PaymentSuccess'

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')

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
