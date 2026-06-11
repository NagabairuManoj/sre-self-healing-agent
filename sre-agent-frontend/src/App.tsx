import { useState, useEffect } from 'react';
import { 
  ShieldAlert, 
  CheckCircle, 
  AlertTriangle, 
  Settings as SettingsIcon, 
  Database, 
  Terminal, 
  Play, 
  ChevronDown, 
  Code, 
  Layers, 
  RefreshCw, 
  ToggleLeft, 
  ToggleRight, 
  Info,
  LogOut,
  User,
  ExternalLink,
  Workflow
} from 'lucide-react';

// Custom SVG GitHub Icon since branding logos were removed from Lucide core
const GithubIcon = ({ size = 18 }: { size?: number }) => (
  <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    stroke="currentColor"
    strokeWidth="2"
    fill="none"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
  </svg>
);

// Interfaces
interface Repository {
  id: number;
  name: string;
  branch: string;
  webhookConnected: boolean;
  healingEnabled: boolean;
}

interface FileModification {
  filepath: string;
  action: 'write' | 'patch';
  content: string;
}

interface HealingRun {
  runId: string;
  jobId: string;
  jobName: string;
  repo: string;
  branch: string;
  timestamp: string;
  status: 'diagnosing' | 'healing' | 'resolved' | 'failed';
  explanation: string;
  modifications: FileModification[];
  prUrl?: string;
}

// Initial Mock Repositories
const INITIAL_REPOS: Repository[] = [
  { id: 1, name: 'NagabairuManoj/demo-failing-infrastructure', branch: 'master', webhookConnected: true, healingEnabled: true },
  { id: 2, name: 'NagabairuManoj/sre-agent-core', branch: 'main', webhookConnected: true, healingEnabled: false }
];

// Initial Mock Healing Runs
const INITIAL_RUNS: HealingRun[] = [
  {
    runId: '27138737437',
    jobId: '80097894615',
    jobName: 'Terraform Init and Plan',
    repo: 'NagabairuManoj/demo-failing-infrastructure',
    branch: 'master',
    timestamp: '2026-06-08 18:19:19',
    status: 'resolved',
    explanation: 'The terraform plan failed because var.bucket_name was referenced in main.tf but never declared in a variables block. We solved this by appending the variable declaration block for bucket_name directly into main.tf.',
    modifications: [
      {
        filepath: 'main.tf',
        action: 'write',
        content: `@@ -20,9 +20,15 @@
 resource "aws_s3_bucket" "demo_bucket" {
-  bucket = var.bucket_name
+  bucket = var.bucket_name
 
   tags = {
     Name        = "Demo Failing Bucket"
     Environment = "Dev"
   }
 }
+
+variable "bucket_name" {
+  type        = string
+  description = "The name of the S3 bucket to provision"
+  default     = "demo-failing-bucket-12345"
+}`
      }
    ],
    prUrl: 'https://github.com/NagabairuManoj/demo-failing-infrastructure/pull/2'
  },
  {
    runId: '27137516826',
    jobId: '80093610208',
    jobName: 'Terraform Init and Plan',
    repo: 'NagabairuManoj/demo-failing-infrastructure',
    branch: 'master',
    timestamp: '2026-06-08 17:56:28',
    status: 'resolved',
    explanation: 'The terraform plan failed because var.bucket_name was referenced but never declared. We declare the bucket_name variable inside main.tf directly to resolve the issue.',
    modifications: [
      {
        filepath: 'main.tf',
        action: 'write',
        content: `@@ -20,9 +20,13 @@
 resource "aws_s3_bucket" "demo_bucket" {
-  bucket = var.bucket_name
+  bucket = var.bucket_name
 }
+
+variable "bucket_name" {
+  type    = string
+  default = "demo-s3-bucket-manoj"
+}`
      }
    ],
    prUrl: 'https://github.com/NagabairuManoj/demo-failing-infrastructure/pull/1'
  }
];

export default function App() {
  // App States
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [activeTab, setActiveTab] = useState<'repos' | 'history' | 'settings'>('repos');
  const [isMockMode, setIsMockMode] = useState(false); // Default to live mode in production
  const [backendUrl, setBackendUrl] = useState(() => {
    const hostname = window.location.hostname;
    return (hostname === 'localhost' || hostname === '127.0.0.1')
      ? 'http://localhost:8000'
      : `${window.location.origin}/api`;
  });
  const [backendHealthy, setBackendHealthy] = useState(false);
  const [customInstructions, setCustomInstructions] = useState('Ensure clean syntax. For Terraform, always append variables if missing.');
  
  // Data States
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [runs, setRuns] = useState<HealingRun[]>([]);
  const [user, setUser] = useState<{ name: string; username: string; avatarUrl: string } | null>(null);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);
  const [isSimulating, setIsSimulating] = useState(false);

  // Extract JWT token from redirect URL or local storage on load
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');
    if (token) {
      localStorage.setItem('token', token);
      // Clean query params
      window.history.replaceState({}, document.title, window.location.pathname);
      setIsLoggedIn(true);
    } else {
      const existingToken = localStorage.getItem('token');
      if (existingToken) {
        setIsLoggedIn(true);
      }
    }
  }, []);

  // Check Backend Health
  useEffect(() => {
    if (isMockMode) {
      setBackendHealthy(true);
      return;
    }

    const checkHealth = async () => {
      try {
        const response = await fetch(`${backendUrl}/health`);
        if (response.ok) {
          setBackendHealthy(true);
        } else {
          setBackendHealthy(false);
        }
      } catch (err) {
        setBackendHealthy(false);
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 10000);
    return () => clearInterval(interval);
  }, [backendUrl, isMockMode]);

  // Fetch User profile, repositories, and run logs
  useEffect(() => {
    if (isMockMode || !isLoggedIn) {
      setRepositories(INITIAL_REPOS);
      setRuns(INITIAL_RUNS);
      setUser({
        name: "Nagabairu Manoj",
        username: "NagabairuManoj",
        avatarUrl: ""
      });
      return;
    }

    const fetchData = async () => {
      const token = localStorage.getItem('token');
      if (!token) {
        setIsLoggedIn(false);
        return;
      }
      try {
        const headers = { 'Authorization': `Bearer ${token}` };
        
        // 1. Fetch user profile
        const profileRes = await fetch(`${backendUrl}/auth/me`, { headers });
        if (!profileRes.ok) throw new Error("Unauthorized");
        const profileData = await profileRes.json();
        setUser({
          name: profileData.name,
          username: profileData.username,
          avatarUrl: profileData.avatar_url
        });

        // 2. Fetch user repositories
        const reposRes = await fetch(`${backendUrl}/repos`, { headers });
        if (reposRes.ok) {
          const reposData = await reposRes.json();
          setRepositories(reposData);
        }

        // 3. Fetch runs history
        const runsRes = await fetch(`${backendUrl}/history`, { headers });
        if (runsRes.ok) {
          const runsData = await runsRes.json();
          setRuns(runsData);
        }
      } catch (err) {
        console.error("Error fetching data:", err);
        localStorage.removeItem('token');
        setIsLoggedIn(false);
      }
    };

    fetchData();
  }, [isLoggedIn, isMockMode, backendUrl]);

  // Poll running jobs if active
  useEffect(() => {
    if (isMockMode || !isLoggedIn) return;

    const hasActiveJobs = runs.some(r => r.status === 'diagnosing' || r.status === 'healing');
    if (!hasActiveJobs) return;

    const interval = setInterval(async () => {
      const token = localStorage.getItem('token');
      if (!token) return;
      try {
        const runsRes = await fetch(`${backendUrl}/history`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (runsRes.ok) {
          const runsData = await runsRes.json();
          setRuns(runsData);
        }
      } catch (err) {
        console.error("Failed to poll runs status", err);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [runs, isMockMode, isLoggedIn, backendUrl]);

  // Simulate SRE Flow (Interactive Mock Run or DB Run)
  const triggerSimulation = async (repoId?: number) => {
    if (isMockMode || !repoId) {
      if (isSimulating) return;
      setIsSimulating(true);
      setActiveTab('history');

      const newRunId = String(Math.floor(Math.random() * 10000000000) + 20000000000);
      const newJobId = String(Math.floor(Math.random() * 10000000000) + 80000000000);
      
      // 1. Diagnosing State
      const runDiagnostics: HealingRun = {
        runId: newRunId,
        jobId: newJobId,
        jobName: 'Terraform Init and Plan',
        repo: 'NagabairuManoj/demo-failing-infrastructure',
        branch: 'master',
        timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
        status: 'diagnosing',
        explanation: 'SRE agent is currently downloading failed GitHub Action logs and analyzing the root cause...',
        modifications: []
      };

      setRuns(prev => [runDiagnostics, ...prev]);
      setExpandedRun(newRunId);

      // 2. Transition to Healing State after 3 seconds
      setTimeout(() => {
        setRuns(prev => prev.map(r => {
          if (r.runId === newRunId) {
            return {
              ...r,
              status: 'healing',
              explanation: 'LLM diagnosed a missing input variable declaration: "bucket_name". The agent is checking out a new branch "fix/failed-run-' + newRunId + '", writing the variable definition block, and staging changes...'
            };
          }
          return r;
        }));
      }, 3000);

      // 3. Transition to Resolved State after 6 seconds (Adds Diff and PR url)
      setTimeout(() => {
        setRuns(prev => prev.map(r => {
          if (r.runId === newRunId) {
            return {
              ...r,
              status: 'resolved',
              explanation: 'Root cause identified: main.tf referenced var.bucket_name without an HCL variable declaration block. SRE Agent declared variable "bucket_name" with a default value. Applied patch, pushed fix to branch, and successfully created a Pull Request.',
              modifications: [
                {
                  filepath: 'main.tf',
                  action: 'write',
                  content: `@@ -20,9 +20,13 @@\n resource "aws_s3_bucket" "demo_bucket" {\n-  bucket = var.bucket_name\n+  bucket = var.bucket_name\n }\n+\n+variable "bucket_name" {\n+  type    = string\n+  default = "demo-s3-bucket-simulated"\n+}`
                }
              ],
              prUrl: `https://github.com/NagabairuManoj/demo-failing-infrastructure/pull/${runs.length + 1}`
            };
          }
          return r;
        }));
        setIsSimulating(false);
      }, 6000);
      return;
    }

    // Real simulation backend call
    const token = localStorage.getItem('token');
    if (!token) return;
    try {
      setIsSimulating(true);
      setActiveTab('history');
      
      const response = await fetch(`${backendUrl}/repos/${repoId}/simulate-failure`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (response.ok) {
        // Fetch updated runs history
        const runsRes = await fetch(`${backendUrl}/history`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (runsRes.ok) {
          const runsData = await runsRes.json();
          setRuns(runsData);
          if (runsData.length > 0) {
            setExpandedRun(runsData[0].runId);
          }
        }
      }
    } catch (err) {
      console.error("Failed to trigger simulation", err);
    } finally {
      setIsSimulating(false);
    }
  };

  const handleToggleHealing = async (id: number) => {
    if (isMockMode) {
      setRepositories(prev => prev.map(repo => {
        if (repo.id === id) {
          return { ...repo, healingEnabled: !repo.healingEnabled };
        }
        return repo;
      }));
      return;
    }

    const token = localStorage.getItem('token');
    if (!token) return;
    try {
      const response = await fetch(`${backendUrl}/repos/${id}/toggle-healing`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (response.ok) {
        const data = await response.json();
        setRepositories(prev => prev.map(repo => {
          if (repo.id === id) {
            return { ...repo, healingEnabled: data.healingEnabled };
          }
          return repo;
        }));
      }
    } catch (err) {
      console.error("Failed to toggle healing", err);
    }
  };

  const handleLogin = async () => {
    try {
      const response = await fetch(`${backendUrl}/auth/login`);
      const data = await response.json();
      if (data.url) {
        window.location.href = data.url;
      }
    } catch (err) {
      console.error("Failed to login", err);
      window.location.href = `${backendUrl}/auth/login`;
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    setUser(null);
    setIsLoggedIn(false);
  };

  const toggleMockMode = () => {
    setIsMockMode(prev => !prev);
  };

  if (!isLoggedIn) {
    return (
      <div className="landing-view">
        <div className="hero-section">
          <div className="hero-badge">
            <Terminal size={14} /> pipeline-agent.tech is live
          </div>
          <h1 className="hero-title">
            Autonomous Self-Healing <br />
            for CI/CD Pipelines
          </h1>
          <p className="hero-description">
            Never debug failing pipelines or drifted terraform code again. Our AI-driven SRE Agent listens to GitHub Webhooks, downloads logs, diagnoses root causes, and automatically opens Pull Requests with the code repairs.
          </p>
          
          <button className="btn btn-primary" onClick={handleLogin}>
            <GithubIcon size={18} /> Connect with GitHub
          </button>
        </div>

        <div className="features-grid">
          <div className="feature-card glass-card">
            <div className="feature-icon">
              <Workflow size={20} />
            </div>
            <h3>Webhook Triggered</h3>
            <p>Listens for failing Actions workflows. Instantly wakes up the agent core to analyze logs.</p>
          </div>
          <div className="feature-card glass-card">
            <div className="feature-icon">
              <Code size={20} />
            </div>
            <h3>LLM Code Repairs</h3>
            <p>Utilizes Gemini LLM to diagnose logs and write code fixes (HCL, Python, Shell) automatically.</p>
          </div>
          <div className="feature-card glass-card">
            <div className="feature-icon">
              <ShieldAlert size={20} />
            </div>
            <h3>PR Summaries</h3>
            <p>Pushes clean branches and opens Pull Requests detailing exactly why it failed and how it was fixed.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="logo-section">
          <div className="logo-icon">P</div>
          <span className="logo-text">pipeline-agent</span>
        </div>

        <ul className="nav-links">
          <li 
            className={`nav-item ${activeTab === 'repos' ? 'active' : ''}`}
            onClick={() => setActiveTab('repos')}
          >
            <Layers size={18} /> Repositories
          </li>
          <li 
            className={`nav-item ${activeTab === 'history' ? 'active' : ''}`}
            onClick={() => setActiveTab('history')}
          >
            <Terminal size={18} /> Healing Logs
          </li>
          <li 
            className={`nav-item ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveTab('settings')}
          >
            <SettingsIcon size={18} /> Configuration
          </li>
        </ul>

        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="user-avatar">
              {user?.avatarUrl ? (
                <img src={user.avatarUrl} alt={user.name} style={{ width: '100%', height: '100%', borderRadius: '50%' }} />
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                  <User size={18} color="#9ca3af" />
                </div>
              )}
            </div>
            <div className="user-info">
              <h4>{user?.name || 'Loading...'}</h4>
              <p>@{user?.username || 'user'}</p>
            </div>
            <button 
              onClick={handleLogout} 
              style={{ background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', marginLeft: 'auto' }}
              title="Logout"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        <header className="top-bar">
          <h2 className="view-title">
            {activeTab === 'repos' && 'Managed Repositories'}
            {activeTab === 'history' && 'Auto-Healing Logs'}
            {activeTab === 'settings' && 'Global Configurations'}
          </h2>

          <div className="top-bar-actions">
            <div className="mode-toggle-banner" style={{ margin: 0, padding: '6px 14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Database size={14} color={isMockMode ? '#10b981' : '#6366f1'} />
                <span style={{ fontSize: '12px', fontWeight: 600 }}>
                  {isMockMode ? 'Mock Simulation Active' : 'Live Mode Connected'}
                </span>
              </div>
            </div>
          </div>
        </header>

        <div className="view-container">
          {/* Active Tab Views */}

          {activeTab === 'repos' && (
            <div>
              <div className="grid-2">
                {repositories.map(repo => (
                  <div className="glass-card" key={repo.id}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px', minWidth: 0, width: '100%' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', flex: 1, minWidth: 0 }}>
                        <h3 style={{ fontSize: '15px', fontWeight: 600, color: '#fff', overflowWrap: 'break-word', wordBreak: 'break-word', lineHeight: '1.4', paddingRight: '12px' }} title={repo.name}>
                          {repo.name}
                        </h3>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginTop: '2px' }}>
                          <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            Branch: <code>{repo.branch}</code>
                          </span>
                          <span style={{ color: 'var(--text-muted)', fontSize: '10px' }}>•</span>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '11px', fontWeight: 500, color: repo.webhookConnected ? '#10b981' : '#f59e0b' }}>
                            <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: repo.webhookConnected ? '#10b981' : '#f59e0b', boxShadow: repo.webhookConnected ? '0 0 6px #10b981' : '0 0 6px #f59e0b' }} />
                            {repo.webhookConnected ? 'Webhook Active' : 'Webhook Pending'}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '24px', paddingTop: '16px', borderTop: '1px solid var(--border-color)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '14px', color: varString('text-secondary') }}>Auto-Healing Status</span>
                      </div>
                      <label className="switch">
                        <input 
                          type="checkbox" 
                          checked={repo.healingEnabled} 
                          onChange={() => handleToggleHealing(repo.id)}
                        />
                        <span className="slider"></span>
                      </label>
                    </div>

                    {repo.healingEnabled && (
                      <div style={{ marginTop: '16px' }}>
                        <button 
                          className="btn btn-secondary" 
                          onClick={() => triggerSimulation(repo.id)} 
                          disabled={isSimulating}
                          style={{ width: '100%', justifyContent: 'center' }}
                        >
                          <Play size={14} /> Trigger Failure Simulation
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'history' && (
            <div className="run-logs-list">
              {runs.length === 0 ? (
                <div className="glass-card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-secondary)' }}>
                  <Terminal size={40} style={{ marginBottom: '16px', opacity: 0.5, marginLeft: 'auto', marginRight: 'auto', display: 'block' }} />
                  <h3 style={{ fontSize: '18px', fontWeight: 600, color: '#fff' }}>No Healing Logs Found</h3>
                  <p style={{ fontSize: '14px', marginTop: '8px', color: 'var(--text-secondary)' }}>
                    When a GitHub actions workflow failure occurs on your connected repositories, the SRE Agent will analyze it and display the repair process here.
                  </p>
                </div>
              ) : (
                runs.map(run => (
                  <div className="glass-card" key={run.runId}>
                  <div 
                    className="log-item-header"
                    onClick={() => setExpandedRun(expandedRun === run.runId ? null : run.runId)}
                  >
                    <div className="log-meta-left">
                      {run.status === 'resolved' && <CheckCircle size={20} color="var(--color-success)" />}
                      {run.status === 'diagnosing' && <div className="spinner"></div>}
                      {run.status === 'healing' && <RefreshCw size={20} color="var(--color-warning)" className="spinner" />}
                      {run.status === 'failed' && <AlertTriangle size={20} color="var(--color-error)" />}
                      
                      <div className="log-title-info">
                        <h3>Run #{run.runId} - {run.jobName}</h3>
                        <p>{run.repo} • {run.branch} • {run.timestamp}</p>
                      </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      <span className={`badge ${
                        run.status === 'resolved' ? 'badge-success' :
                        run.status === 'diagnosing' ? 'badge-info' :
                        run.status === 'healing' ? 'badge-warning' : 'badge-error'
                      }`}>
                        {run.status}
                      </span>
                      <ChevronDown 
                        size={16} 
                        className={`log-expand-icon ${expandedRun === run.runId ? 'expanded' : ''}`}
                      />
                    </div>
                  </div>

                  {expandedRun === run.runId && (
                    <div className="log-details-expanded">
                      <div className="analysis-card">
                        <h4>
                          <Info size={14} color="var(--color-primary)" /> SRE Auto-Healing Analysis
                        </h4>
                        <p>{run.explanation}</p>
                      </div>

                      {run.modifications.length > 0 && (
                        <div>
                          <h4 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '8px', color: '#fff' }}>Applied Changes</h4>
                          {run.modifications.map((mod, i) => (
                            <div className="diff-container" key={i}>
                              <div className="diff-header">
                                <span>{mod.filepath}</span>
                                <span className="badge badge-success">{mod.action}</span>
                              </div>
                              <div className="diff-body">
                                {mod.content.split('\n').map((line, idx) => {
                                  let type = 'normal';
                                  if (line.startsWith('+')) type = 'addition';
                                  else if (line.startsWith('-')) type = 'deletion';
                                  
                                  return (
                                    <div className={`diff-line ${type}`} key={idx}>
                                      <span className="diff-sign">
                                        {line.startsWith('+') && '+'}
                                        {line.startsWith('-') && '-'}
                                      </span>
                                      {line.startsWith('+') || line.startsWith('-') ? line.substring(1) : line}
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}

                      {run.prUrl && (
                        <div style={{ marginTop: '8px' }}>
                          <a 
                            href={run.prUrl} 
                            target="_blank" 
                            rel="noopener noreferrer" 
                            className="btn btn-primary"
                            style={{ textDecoration: 'none' }}
                          >
                            View Pull Request <ExternalLink size={14} />
                          </a>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )))}
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="glass-card" style={{ maxWidth: '600px' }}>
              <div className="mode-toggle-banner">
                <div className="mode-toggle-info">
                  <h4>
                    <Database size={16} /> Mode Selector
                  </h4>
                  <p>Switch between Mock Offline Simulation and Production API integration.</p>
                </div>
                <button 
                  onClick={toggleMockMode} 
                  style={{ background: 'none', border: 'none', cursor: 'pointer' }}
                >
                  {isMockMode ? (
                    <ToggleLeft size={36} color="var(--text-secondary)" />
                  ) : (
                    <ToggleRight size={36} color="var(--color-primary)" />
                  )}
                </button>
              </div>

              {!isMockMode && (
                <div className="form-group">
                  <label className="form-label">Backend Connection URL</label>
                  <div style={{ display: 'flex', gap: '12px' }}>
                    <input 
                      type="text" 
                      className="form-input" 
                      value={backendUrl} 
                      onChange={(e) => setBackendUrl(e.target.value)}
                    />
                    <span className={`badge ${backendHealthy ? 'badge-success' : 'badge-error'}`} style={{ alignSelf: 'center' }}>
                      {backendHealthy ? 'online' : 'offline'}
                    </span>
                  </div>
                </div>
              )}

              <div className="form-group">
                <label className="form-label">SRE System Prompts & Instructions</label>
                <textarea 
                  className="form-input" 
                  rows={4} 
                  value={customInstructions} 
                  onChange={(e) => setCustomInstructions(e.target.value)}
                  style={{ resize: 'vertical' }}
                />
                <p style={{ fontSize: '11px', color: varString('text-muted'), marginTop: '6px' }}>
                  These instructions are injected into the LLM Reasoning Engine context to guide auto-healing suggestions.
                </p>
              </div>

              <div className="form-group">
                <label className="form-label">LLM Reasoning Engine model</label>
                <select className="form-input" defaultValue="gemini-2.5-flash">
                  <option value="gemini-2.5-flash">Gemini 2.5 Flash (Default)</option>
                  <option value="gemini-1.5-flash">Gemini 1.5 Flash</option>
                  <option value="gemini-1.5-pro">Gemini 1.5 Pro</option>
                </select>
              </div>

              <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }}>
                Save Configurations
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

// Small helper for CSS variables access in React styles
function varString(name: string): string {
  return `var(--${name})`;
}
