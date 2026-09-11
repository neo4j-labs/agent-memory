import {
  Badge,
  Box,
  Button,
  Card,
  Flex,
  Heading,
  SimpleGrid,
  Spinner,
  Table,
  Text,
} from '@chakra-ui/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FiAlertTriangle, FiCheckCircle, FiClock } from 'react-icons/fi'
import { alertApi, countByStatus } from '../../lib/api'

/** Analyst acknowledging alerts. A real deployment reads this from auth. */
const ANALYST_ID = 'analyst-demo'

function severityPalette(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'red'
    case 'high':
      return 'orange'
    case 'medium':
      return 'yellow'
    case 'low':
      return 'green'
    default:
      return 'gray'
  }
}

function statusPalette(status: string): string {
  switch (status.toLowerCase()) {
    case 'new':
      return 'brand'
    case 'investigating':
    case 'under_review':
      return 'purple'
    case 'escalated':
      return 'red'
    case 'acknowledged':
      return 'teal'
    case 'resolved':
    case 'closed':
      return 'gray'
    default:
      return 'gray'
  }
}

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
}

export default function AlertsPanel() {
  const queryClient = useQueryClient()

  const { data: alerts = [], isLoading } = useQuery({
    queryKey: ['alerts'],
    queryFn: () => alertApi.list(),
  })

  const { data: summary } = useQuery({
    queryKey: ['alertSummary'],
    queryFn: () => alertApi.getSummary(),
  })

  // PATCH /alerts/{id} with status=ACKNOWLEDGED - there is no /acknowledge route.
  const acknowledge = useMutation({
    mutationFn: (alertId: string) => alertApi.acknowledge(alertId, ANALYST_ID),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
      void queryClient.invalidateQueries({ queryKey: ['alertSummary'] })
    },
  })

  return (
    <Box>
      <Flex justify="space-between" align="center" mb={6}>
        <Box>
          <Heading size="lg" fontFamily="heading">
            Alerts
          </Heading>
          <Text color="fg.muted">Compliance alerts and notifications</Text>
        </Box>
      </Flex>

      {/* Summary */}
      <SimpleGrid columns={{ base: 1, md: 3 }} gap={6} mb={8}>
        {[
          { label: 'New Alerts', value: countByStatus(summary, 'new'), icon: FiAlertTriangle, palette: 'brand' },
          {
            label: 'Investigating',
            value: countByStatus(summary, 'investigating'),
            icon: FiClock,
            palette: 'orange',
          },
          {
            label: 'Resolved',
            value: countByStatus(summary, 'resolved'),
            icon: FiCheckCircle,
            palette: 'green',
          },
        ].map(({ label, value, icon: Icon, palette }) => (
          <Card.Root key={label} colorPalette={palette}>
            <Card.Body>
              <Flex align="center" gap={4}>
                <Box p={3} borderRadius="full" bg="colorPalette.muted" color="colorPalette.fg">
                  <Icon size={24} />
                </Box>
                <Box>
                  <Text color="fg.muted" fontSize="sm">
                    {label}
                  </Text>
                  <Text fontSize="2xl" fontWeight="bold">
                    {value}
                  </Text>
                </Box>
              </Flex>
            </Card.Body>
          </Card.Root>
        ))}
      </SimpleGrid>

      {/* Table */}
      <Card.Root>
        <Card.Header>
          <Heading size="md" fontFamily="heading">
            All Alerts
          </Heading>
        </Card.Header>
        <Card.Body>
          {isLoading ? (
            <Flex justify="center" py={8}>
              <Spinner size="lg" colorPalette="brand" />
            </Flex>
          ) : alerts.length === 0 ? (
            <Text color="fg.muted" textAlign="center" py={8}>
              No alerts found. Load the sample data with <code>make load-data</code>.
            </Text>
          ) : (
            <Box overflowX="auto">
              <Table.Root>
                <Table.Header>
                  <Table.Row>
                    <Table.ColumnHeader>ID</Table.ColumnHeader>
                    <Table.ColumnHeader>Title</Table.ColumnHeader>
                    <Table.ColumnHeader>Type</Table.ColumnHeader>
                    <Table.ColumnHeader>Severity</Table.ColumnHeader>
                    <Table.ColumnHeader>Status</Table.ColumnHeader>
                    <Table.ColumnHeader>Customer</Table.ColumnHeader>
                    <Table.ColumnHeader>Created</Table.ColumnHeader>
                    <Table.ColumnHeader>Actions</Table.ColumnHeader>
                  </Table.Row>
                </Table.Header>
                <Table.Body>
                  {alerts.map((alert) => (
                    <Table.Row key={alert.id}>
                      <Table.Cell fontFamily="mono" fontSize="sm">
                        {alert.id}
                      </Table.Cell>
                      <Table.Cell fontWeight="medium" maxW="200px" truncate>
                        {alert.title}
                      </Table.Cell>
                      <Table.Cell>
                        <Text fontSize="sm" textTransform="capitalize">
                          {alert.type.replace(/_/g, ' ')}
                        </Text>
                      </Table.Cell>
                      <Table.Cell>
                        <Badge colorPalette={severityPalette(alert.severity)}>
                          {alert.severity}
                        </Badge>
                      </Table.Cell>
                      <Table.Cell>
                        <Badge colorPalette={statusPalette(alert.status)}>
                          {alert.status.replace(/_/g, ' ')}
                        </Badge>
                      </Table.Cell>
                      <Table.Cell fontFamily="mono" fontSize="sm">
                        {alert.customer_id}
                      </Table.Cell>
                      <Table.Cell fontSize="sm" color="fg.muted">
                        {formatDate(alert.created_at)}
                      </Table.Cell>
                      <Table.Cell>
                        {alert.status.toLowerCase() === 'new' && (
                          <Button
                            size="sm"
                            colorPalette="teal"
                            variant="outline"
                            onClick={() => acknowledge.mutate(alert.id)}
                            loading={acknowledge.isPending && acknowledge.variables === alert.id}
                          >
                            Acknowledge
                          </Button>
                        )}
                      </Table.Cell>
                    </Table.Row>
                  ))}
                </Table.Body>
              </Table.Root>
            </Box>
          )}
          {acknowledge.isError && (
            <Text mt={3} fontSize="sm" color="fg.error">
              Could not acknowledge the alert:{' '}
              {acknowledge.error instanceof Error ? acknowledge.error.message : 'request failed'}
            </Text>
          )}
        </Card.Body>
      </Card.Root>
    </Box>
  )
}
