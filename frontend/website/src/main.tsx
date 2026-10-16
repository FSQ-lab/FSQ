import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { WebsiteApp } from './WebsiteApp';
import './styles.css';

const root = document.getElementById('root');

if (!root) {
  throw new Error('Website root element is missing');
}

createRoot(root).render(
  <StrictMode>
    <WebsiteApp />
  </StrictMode>,
);
