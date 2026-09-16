FROM node:24-alpine
WORKDIR /srv/web
COPY package.json ./
RUN npm install
COPY . ./
EXPOSE 3000
CMD ["npm", "run", "dev"]
