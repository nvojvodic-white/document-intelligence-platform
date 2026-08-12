# Dev server rather than a production build behind nginx. UI polish is
# explicitly not scored here, and `vite dev` keeps the stack to one stage and
# one port with no build artefacts to serve.
FROM node:24-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# --host binds 0.0.0.0 so the port is reachable from outside the container;
# vite binds localhost by default, which inside a container means nothing.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
