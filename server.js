const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const bodyParser = require('body-parser');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

// Middleware
app.use(bodyParser.json());

// WebSocket Connection
wss.on('connection', (ws) => {
    console.log('New client connected');
    ws.on('message', (message) => {
        console.log(`Received message: ${message}`);
        // Handle message from the dashboard
    });
    
    ws.on('close', () => {
        console.log('Client disconnected');
    });
});

// Endpoint for ESP32 data reception
app.post('/esp32/data', (req, res) => {
    const esp32Data = req.body;
    console.log('Received ESP32 data:', esp32Data);
    // Process ESP32 data here
    res.status(200).send('Data received');
});

// Endpoint for ML result handling
app.post('/ml/results', (req, res) => {
    const mlResults = req.body;
    console.log('Received ML results:', mlResults);
    // Handle ML results here
    res.status(200).send('ML results processed');
});

// DeepSeek AI chat integration
app.post('/deepseek/chat', (req, res) => {
    const chatMessage = req.body;
    console.log('Received chat message:', chatMessage);
    // Integrate with DeepSeek AI chat here
    res.status(200).send('Chat message processed');
});

// Start the server
const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Server is running on port ${PORT}`);
});
