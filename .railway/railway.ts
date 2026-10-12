import {defineRailway, project, redis, service, github, volume} from 'railway/iac';
export default defineRailway(() => {
  const cache = redis('Redis');
  const media = volume('lofi-media', {sizeMB: 5000});
  const studio = service('lofi-studio', {
    source: github('coldzeeyt/lofistudio', {branch: 'main'}),
    healthcheck: '/api/health',
    healthcheckTimeout: 120,
    replicas: 1,
    volumeMounts: {'/data': media},
    env: {
      REDIS_URL: cache.env.REDIS_URL,
      DATA_DIR: '/data', PORT: '8000', MAX_STORAGE_GB: '4',
      MAX_UPLOAD_MB: '250', MAX_ACTIVE_JOBS: '3',
      MAX_RENDER_SECONDS: '10800', MAX_DAILY_RENDER_SECONDS: '21600', RETENTION_HOURS: '24',
    },
  });
  return project('Lofi Studio', {resources: [cache, media, studio]});
});
