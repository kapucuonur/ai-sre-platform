import React, { useEffect, useState } from 'react'
import { CheckCircle, Copy, Check, Loader2, ArrowRight } from 'lucide-react'
import { API_BASE } from '../config'

function PaymentSuccess() {
  const [loading, setLoading] = useState(true)
  const [subData, setSubData] = useState(null)
  const [copiedKey, setCopiedKey] = useState(false)
  const [copiedCmd, setCopiedCmd] = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sessionId = params.get('session_id')

    if (!sessionId) {
      setLoading(false)
      return
    }

    let intervalId
    const pollSubscription = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/stripe/subscription?session_id=${sessionId}`)
        const data = await res.json()
        if (data && data.status === 'active') {
          setSubData(data)
          setLoading(false)
          clearInterval(intervalId)
        }
      } catch (err) {
        console.error('Failed to poll subscription:', err)
      }
    }

    // Run first call immediately
    pollSubscription()
    // Poll every 2 seconds
    intervalId = setInterval(pollSubscription, 2000)

    return () => clearInterval(intervalId)
  }, [])

  const copyToClipboard = (text, setCopied) => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleFinish = () => {
    // Clear URL parameters and refresh/navigate to dashboard
    window.location.href = '/'
  }

  if (loading) {
    return (
      <div className="payment-loading-container">
        <Loader2 className="spinner-icon" />
        <h3>Verifying Payment</h3>
        <p>Waiting for Stripe checkout verification. Generating SRE API Key...</p>
      </div>
    )
  }

  if (!subData) {
    return (
      <div className="payment-error-container">
        <h3 style={{ color: '#ef4444' }}>Verification Failed</h3>
        <p>Could not locate active checkout session. Please try again or contact support.</p>
        <button className="btn-primary-custom" onClick={handleFinish}>Go to Platform</button>
      </div>
    )
  }

  const installCommand = `curl -sSL https://sre-daemon.com/install.sh | SRE_API_KEY=${subData.api_key} bash`

  return (
    <div className="payment-success-card">
      <div className="success-header">
        <div className="success-icon-wrapper">
          <CheckCircle size={48} className="success-icon" />
        </div>
        <h2>Subscription Active!</h2>
        <p className="plan-badge-custom">{subData.plan.toUpperCase()} PLAN activated successfully</p>
      </div>

      <div className="success-body">
        <div className="onboarding-step">
          <h4>1. Your SRE API Key</h4>
          <p>Use this key to authenticate your daemon with the SRE Platform.</p>
          <div className="code-box">
            <code>{subData.api_key}</code>
            <button 
              onClick={() => copyToClipboard(subData.api_key, setCopiedKey)}
              className="copy-button"
            >
              {copiedKey ? <Check size={16} style={{ color: '#10b981' }} /> : <Copy size={16} />}
            </button>
          </div>
        </div>

        <div className="onboarding-step" style={{ marginTop: '20px' }}>
          <h4>2. Run Installation Command</h4>
          <p>Execute this command directly on your target server (e.g. Raspberry Pi or VPS):</p>
          <div className="code-box cmd-box">
            <code>{installCommand}</code>
            <button 
              onClick={() => copyToClipboard(installCommand, setCopiedCmd)}
              className="copy-button"
            >
              {copiedCmd ? <Check size={16} style={{ color: '#10b981' }} /> : <Copy size={16} />}
            </button>
          </div>
        </div>
      </div>

      <button className="btn-primary-custom finish-btn" onClick={handleFinish}>
        Launch Platform Dashboard <ArrowRight size={16} />
      </button>
    </div>
  )
}

export default PaymentSuccess
