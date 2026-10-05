'use client';

import './ShapeWaves.css';

const ShapeWaves = ({
  text = '',
  color = '#4A4E69',
  hoverColor = '#F9F7F7',
  backgroundColor = '#22223B',
  speed = 1,
  glow = 0.35
}) => {
  const bgStyle: React.CSSProperties = {
    backgroundColor,
    backgroundImage: `
      radial-gradient(circle at 15% 20%, rgba(74, 78, 105, 0.15) 0%, transparent 40%),
      radial-gradient(circle at 85% 80%, rgba(242, 169, 0, 0.1) 0%, transparent 35%),
      radial-gradient(circle at 50% 50%, rgba(255, 255, 255, 0.03) 0%, transparent 50%)
    `,
    animation: `ripple ${15 / speed}s ease-in-out infinite`
  };

  return (
    <div className="shape-waves" style={bgStyle}>
      {text && <span className="shape-waves-text">{text}</span>}
      <div className="shape-waves-overlay" data-glow={glow ? '' : undefined} />
    </div>
  );
};

export default ShapeWaves;
