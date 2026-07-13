import React, { useState, useEffect } from 'react'
import { Save, Key, Slack, Eye, EyeOff, Shield } from 'lucide-react'
import { API_BASE } from '../config'

function Settings() {
  const [form, setForm] = useState({
    gemini_api_key: '',
    slack_bot_token: '',
    slack_channel_id: '',
    autonomous_mode: false
  })
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState(null)
  const [showKeys, setShowKeys] = useState({
    gemini: false,
    slack: false
  })

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`)
      .then((res) => res.json())
      .then((data) => {
        setForm({
          gemini_api_key: data.gemini_api_key || '',
          slack_bot_token: data.slack_bot_token || '',
          slack_channel_id: data.slack_channel_id || '',
          autonomous_mode: !!data.autonomous_mode
        })
        setLoading(false)
      })
      .catch((err) => {
        console.error('Error fetching settings:', err)
        setLoading(false)
      })
  }, [])

  const handleSubmit = (e) => {
    e.preventDefault()
    setMessage({ type: 'info', text: 'Saving settings...' })

    fetch(`${API_BASE}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form)
    })
      .then((res) => {
        if (!res.ok) throw new Error('Failed to save settings')
        return res.json()
      })
      .then(() => {
        setMessage({ type: 'success', text: 'Settings saved successfully! ✅' })
        setTimeout(() => setMessage(null), 3000)
      })
      .catch((err) => {
        setMessage({ type: 'error', text: `Error: ${err.message} ❌` })
      })
  }

  if (loading) {
    return <p className="page-subtitle">Settings are loading...</p>
  }

  return (
    <div style={{ maxWidth: '600px' }}>
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
        <p className="page-subtitle">Configure AI API keys, Slack tokens, and ChatOps notification destinations.</p>
      </div>

      {message && (
        <div 
         className="card" 
          style={{ 
            padding: '1rem', 
            marginBottom: '1.5rem',
            borderColor: message.type === 'success' ? 'var(--status-resolved)' : message.type === 'error' ? 'var(--status-rejected)' : 'var(--accent-cyan)'
          }}
        >
          <p style={{ margin: 0, fontWeight: 500 }}>{message.text}</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="card">
        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Key size={16} color="var(--accent-cyan)" /> Google Gemini API Key
          </label>
          <div style={{ position: 'relative' }}>
            <input 
              type={showKeys.gemini ? "text" : "password"}
              className="form-input" 
              value={form.gemini_api_key}
              onChange={(e) => setForm({ ...form, gemini_api_key: e.target.value })}
              placeholder="AIzaSy..."
            />
            <button 
              type="button"
              onClick={() => setShowKeys({ ...showKeys, gemini: !showKeys.gemini })}
              style={{
                position: 'absolute',
                right: '1rem',
                top: '50%',
                transform: 'translateY(-50%)',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--text-secondary)'
              }}
            >
              {showKeys.gemini ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <p className="incident-time" style={{ marginTop: '0.2rem' }}>
            Used for log analysis and proposed remediation commands.
          </p>
        </div>

        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Slack size={16} color="var(--accent-cyan)" /> Slack Bot Token (xoxb)
          </label>
          <div style={{ position: 'relative' }}>
            <input 
              type={showKeys.slack ? "text" : "password"}
              className="form-input" 
              value={form.slack_bot_token}
              onChange={(e) => setForm({ ...form, slack_bot_token: e.target.value })}
              placeholder="xoxb-..."
            />
            <button 
              type="button"
              onClick={() => setShowKeys({ ...showKeys, slack: !showKeys.slack })}
              style={{
                position: 'absolute',
                right: '1rem',
                top: '50%',
                transform: 'translateY(-50%)',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--text-secondary)'
              }}
            >
              {showKeys.slack ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Slack size={16} color="var(--accent-cyan)" /> Slack Channel ID
          </label>
          <input 
            type="text" 
            className="form-input" 
            value={form.slack_channel_id}
            onChange={(e) => setForm({ ...form, slack_channel_id: e.target.value })}
            placeholder="C05..."
          />
          <p className="incident-time" style={{ marginTop: '0.2rem' }}>
            Channel where SRE alert cards will be posted.
          </p>
        </div>

        <div className="form-group" style={{ marginTop: '1.5rem', marginBottom: '1.5rem' }}>
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Shield size={16} color="var(--accent-cyan)" /> Autonomous Self-Healing Mode
            </span>
            <input 
              type="checkbox"
              style={{
                width: '18px',
                height: '18px',
                accentColor: 'var(--accent-cyan)',
                cursor: 'pointer'
              }}
              checked={form.autonomous_mode}
              onChange={(e) => setForm({ ...form, autonomous_mode: e.target.checked })}
            />
          </label>
          <p className="incident-time" style={{ marginTop: '0.2rem' }}>
            When active, the platform will automatically run proposed SRE healing actions without prompting for manual approval.
          </p>
        </div>

        <button type="submit" className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginTop: '0.5rem' }}>
          <Save size={16} /> Save Settings
        </button>
      </form>
    </div>
  )
}

export default Settings
