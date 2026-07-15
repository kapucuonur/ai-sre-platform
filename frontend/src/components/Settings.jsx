import React, { useState, useEffect } from 'react'
import { Save, Key, Slack, Eye, EyeOff, Shield, Zap } from 'lucide-react'
import { API_BASE } from '../config'

function Settings() {
  const [form, setForm] = useState({
    gemini_api_key: '',
    anthropic_api_key: '',
    slack_bot_token: '',
    slack_channel_id: '',
    slack_signing_secret: '',
    autonomous_mode: false
  })
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState(null)
  const [showKeys, setShowKeys] = useState({
    gemini: false,
    anthropic: false,
    slack: false,
    slackSecret: false
  })

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`)
      .then((res) => res.json())
      .then((data) => {
        setForm({
          gemini_api_key: data.gemini_api_key || '',
          anthropic_api_key: data.anthropic_api_key || '',
          slack_bot_token: data.slack_bot_token || '',
          slack_channel_id: data.slack_channel_id || '',
          slack_signing_secret: data.slack_signing_secret || '',
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

  const PasswordField = ({ label, icon, fieldKey, showKey, placeholder, hint }) => (
    <div className="form-group">
      <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        {icon} {label}
      </label>
      <div style={{ position: 'relative' }}>
        <input
          type={showKeys[showKey] ? "text" : "password"}
          className="form-input"
          value={form[fieldKey]}
          onChange={(e) => setForm({ ...form, [fieldKey]: e.target.value })}
          placeholder={placeholder}
        />
        <button
          type="button"
          onClick={() => setShowKeys({ ...showKeys, [showKey]: !showKeys[showKey] })}
          style={{
            position: 'absolute', right: '1rem', top: '50%', transform: 'translateY(-50%)',
            background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)'
          }}
        >
          {showKeys[showKey] ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
      {hint && <p className="incident-time" style={{ marginTop: '0.2rem' }}>{hint}</p>}
    </div>
  )

  if (loading) {
    return <p className="page-subtitle">Settings are loading...</p>
  }

  return (
    <div style={{ maxWidth: '600px' }}>
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
        <p className="page-subtitle">Configure AI API keys, Slack integration, and ChatOps notification destinations.</p>
      </div>

      {message && (
        <div className="card" style={{
          padding: '1rem', marginBottom: '1.5rem',
          borderColor: message.type === 'success' ? 'var(--status-resolved)' : message.type === 'error' ? 'var(--status-rejected)' : 'var(--accent-cyan)'
        }}>
          <p style={{ margin: 0, fontWeight: 500 }}>{message.text}</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="card">

        {/* AI Models */}
        <p style={{ fontWeight: 600, color: 'var(--accent-cyan)', marginBottom: '1rem', fontSize: '0.85rem', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
          🤖 AI Models
        </p>

        <PasswordField
          label="Google Gemini API Key"
          icon={<Key size={16} color="var(--accent-cyan)" />}
          fieldKey="gemini_api_key" showKey="gemini"
          placeholder="AIzaSy..."
          hint="Primary model for log analysis and proposed remediation commands."
        />

        <PasswordField
          label="Anthropic Claude API Key"
          icon={<Zap size={16} color="#d97706" />}
          fieldKey="anthropic_api_key" showKey="anthropic"
          placeholder="sk-ant-..."
          hint="Secondary cascade model. Leave empty to disable Claude entirely."
        />

        {/* Slack Integration */}
        <p style={{ fontWeight: 600, color: 'var(--accent-cyan)', marginTop: '1.5rem', marginBottom: '1rem', fontSize: '0.85rem', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
          💬 Slack Integration
        </p>

        <PasswordField
          label="Slack Bot Token (xoxb)"
          icon={<Slack size={16} color="var(--accent-cyan)" />}
          fieldKey="slack_bot_token" showKey="slack"
          placeholder="xoxb-..."
          hint={null}
        />

        <div className="form-group">
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Slack size={16} color="var(--accent-cyan)" /> Slack Channel ID
          </label>
          <input
            type="text" className="form-input"
            value={form.slack_channel_id}
            onChange={(e) => setForm({ ...form, slack_channel_id: e.target.value })}
            placeholder="C05..."
          />
          <p className="incident-time" style={{ marginTop: '0.2rem' }}>
            Channel where SRE alert cards will be posted.
          </p>
        </div>

        <PasswordField
          label="Slack Signing Secret"
          icon={<Key size={16} color="#a78bfa" />}
          fieldKey="slack_signing_secret" showKey="slackSecret"
          placeholder="abcd1234..."
          hint="Required for Approve/Reject buttons. Found in Slack App → Basic Information."
        />

        {/* Autonomous Mode */}
        <p style={{ fontWeight: 600, color: 'var(--accent-cyan)', marginTop: '1.5rem', marginBottom: '1rem', fontSize: '0.85rem', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
          ⚡ Behaviour
        </p>

        <div className="form-group" style={{ marginBottom: '1.5rem' }}>
          <label className="form-label" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Shield size={16} color="var(--accent-cyan)" /> Autonomous Self-Healing Mode
            </span>
            <input
              type="checkbox"
              style={{ width: '18px', height: '18px', accentColor: 'var(--accent-cyan)', cursor: 'pointer' }}
              checked={form.autonomous_mode}
              onChange={(e) => setForm({ ...form, autonomous_mode: e.target.checked })}
            />
          </label>
          <p className="incident-time" style={{ marginTop: '0.2rem' }}>
            When active, the platform will automatically run proposed SRE healing actions without manual approval.
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
