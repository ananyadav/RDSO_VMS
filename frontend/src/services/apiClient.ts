import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api', // Relative path for deployment with backend
});

// This is an "interceptor" that adds the JWT token to every request
apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('accessToken');
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

export default apiClient;
