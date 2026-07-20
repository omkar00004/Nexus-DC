import { createContext, useContext, useState } from 'react'

// Demo personas - attribution, not authentication. One named person per role;
// the active persona is tagged onto GUIDE sessions (operator) and document
// uploads (uploaded_by). Different roles see the same converged truth - that
// is the point, so there is deliberately no per-role data filtering.
export const PERSONAS = [
  { id: 'pm', name: 'Arjun Mehta', role: 'Project Manager' },
  { id: 'commissioning', name: 'Priya Sharma', role: 'Commissioning Engineer' },
  { id: 'site', name: 'Rohan Kulkarni', role: 'Site Engineer' },
]

const PersonaContext = createContext(null)

export function PersonaProvider({ children }) {
  const [persona, setActive] = useState(() => {
    const saved = localStorage.getItem('nexus_persona')
    return PERSONAS.find((p) => p.id === saved) || PERSONAS[0]
  })
  const setPersona = (id) => {
    const p = PERSONAS.find((x) => x.id === id) || PERSONAS[0]
    localStorage.setItem('nexus_persona', p.id)
    setActive(p)
  }
  return (
    <PersonaContext.Provider value={{ persona, setPersona }}>
      {children}
    </PersonaContext.Provider>
  )
}

export const usePersona = () => useContext(PersonaContext)
