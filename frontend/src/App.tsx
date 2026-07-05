import { BrowserRouter } from 'react-router-dom'
import { SessionProvider } from './hooks/useSession'
import AppRoutes from './routes'

function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </SessionProvider>
  )
}

export default App
