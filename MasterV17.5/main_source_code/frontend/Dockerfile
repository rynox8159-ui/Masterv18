FROM node:22-alpine AS dependencies
RUN corepack enable && corepack prepare yarn@1.22.22 --activate
WORKDIR /app/dependencies/frontend
COPY dependencies/frontend/package.json dependencies/frontend/yarn.lock ./
RUN yarn install --frozen-lockfile --non-interactive

FROM dependencies AS build
WORKDIR /app
COPY main_source_code/frontend ./main_source_code/frontend
RUN ln -s ../../dependencies/frontend/node_modules main_source_code/frontend/node_modules
ARG VITE_BACKEND_URL=http://localhost:7007
ARG REACT_APP_BACKEND_URL=http://localhost:7007
ARG VITE_API_SAME_ORIGIN=false
ENV VITE_BACKEND_URL=${VITE_BACKEND_URL} \
    REACT_APP_BACKEND_URL=${REACT_APP_BACKEND_URL} \
    VITE_API_SAME_ORIGIN=${VITE_API_SAME_ORIGIN}
RUN npm --prefix dependencies/frontend run build

FROM node:22-alpine AS production
WORKDIR /app
ENV NODE_ENV=production
COPY --from=dependencies /app/dependencies/frontend ./dependencies/frontend
COPY --from=build /app/main_source_code/frontend/server.cjs ./main_source_code/frontend/server.cjs
COPY --from=build /app/main_source_code/frontend/dist ./main_source_code/frontend/dist
EXPOSE 3000
CMD ["node", "main_source_code/frontend/server.cjs"]