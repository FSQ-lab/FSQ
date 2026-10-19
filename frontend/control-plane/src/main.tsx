import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { EntryApp } from './app/EntryApp';
import './styles/tokens.css';
import './styles/devices.css';
import './styles/config.css';
import './features/overview/overview.css';

const root = document.getElementById('root');
if (!root) throw new Error('Control Plane root element was not found.');
createRoot(root).render(<StrictMode><EntryApp /></StrictMode>);
