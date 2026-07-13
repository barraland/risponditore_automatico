import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './lib/auth'
import { TenantProvider } from './lib/tenant'
import App from './App'
import './index.css'
import './themes/medicale.css'
import { applicaDesignSystem } from './lib/designSystem'
import { applyUiScale } from './lib/uiScale'

// Applica design system e zoom scelti PRIMA del render (niente flash).
applicaDesignSystem()
applyUiScale()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <TenantProvider>
          <App />
        </TenantProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
