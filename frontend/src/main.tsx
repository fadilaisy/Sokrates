import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Fonts are bundled locally so the cockpit renders correctly offline on the factory network.
import '@fontsource-variable/inter'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
