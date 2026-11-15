import { createRoot } from 'react-dom/client';
import { App } from './App';
import { cargarConfig } from './config';
import './styles.css';

cargarConfig().then((cfg) => {
  createRoot(document.getElementById('root')!).render(<App cfg={cfg} />);
});
