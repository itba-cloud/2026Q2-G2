import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { loadConfig } from './config.ts'

const root = createRoot(document.getElementById('root')!)

// The app needs to know where the API and the Hosted UI are before anything
// renders: config.json, which Terraform writes next to index.html.
loadConfig()
  .then(() =>
    root.render(
      <StrictMode>
        <App />
      </StrictMode>,
    ),
  )
  .catch((error: unknown) => {
    console.error('LGDC: no config.json', error)
    root.render(
      <p style={{ padding: '2rem', fontFamily: 'system-ui' }}>
        El portal no encontró su configuración (config.json). Volvé a correr Deploy.
      </p>,
    )
  })
