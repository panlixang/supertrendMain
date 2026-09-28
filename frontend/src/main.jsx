import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import ResearchPage from './pages/ResearchPage';
import PatternPage from './pages/PatternPage';
import StrategyLearningPage from './pages/StrategyLearningPage';
import StrategyLearningV2Page from './pages/StrategyLearningV2Page';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<HomePage />} />
          <Route path="research" element={<ResearchPage />} />
          <Route path="pattern" element={<PatternPage />} />
          <Route path="strategy-learning" element={<StrategyLearningPage />} />
          <Route path="strategy-learning-v2" element={<StrategyLearningV2Page />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
