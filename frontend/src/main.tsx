import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './lib/auth'
import { TenantProvider } from './lib/tenant'
import App from './App'
import './index.css'
import './themes/medicale.css'
import { applicaDesignSystem } from './lib/designSystem'

// Applica il design system scelto PRIMA del render (niente flash del tema sbagliato).
applicaDesignSystem()

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
