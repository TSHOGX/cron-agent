const { resolve } = require('path');

const SKILL_DIR = resolve(__dirname);

module.exports = {
  apps: [{
    name: 'cron-agent',
    script: 'assets/api.py',
    interpreter: 'python3',
    cwd: SKILL_DIR,
    env: {
      CRON_AGENT_DATA_DIR: `${SKILL_DIR}/.cron_agent_data`,
      PYTHONPATH: `${SKILL_DIR}/assets`
    },
    watch: false,
    autorestart: true,
    port: 18001,
    error_file: `${SKILL_DIR}/.cron_agent_data/logs/pm2-error.log`,
    out_file: `${SKILL_DIR}/.cron_agent_data/logs/pm2-out.log`,
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: true
  }]
};
